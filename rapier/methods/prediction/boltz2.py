"""Boltz-2 biomolecular complex and affinity prediction method."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...artifacts import Artifact
from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...core.task import Task
from ...data import (
    BindingSite, ComplexStructure, DnaSequence, File, Molecule, Molecules, ProteinSequence,
    ProteinStructure, Report, RnaSequence, Scores, Structure,
)
from ..utils import check_executable
from ._validation import structure_errors
from .boltz2_runner import _matching_confidence, build_input_yaml, run_manifest

BOLTZ_SCORE_DIRECTIONS = {
    "confidence_score": "higher",
    "affinity_pred_value": "lower",
    "affinity_probability_binary": "higher",
}


class Boltz2Prediction(Method):
    """Predict biomolecular structures and optional protein-ligand affinity.

    Outputs the predicted complexes, Boltz's confidence/affinity reports, a
    ``scores`` table (one row per model) and, with ``emit_docking_inputs``, a
    receptor and search box ready for a docking method.
    """

    register_name = "boltz2"
    display_name = "Boltz-2"
    category = "prediction"
    version = "2.0"
    inputs = {
        "proteins": PortSpec(ProteinSequence, ("inline", "fasta"), required=False, cardinality="many"),
        "dna": PortSpec(DnaSequence, ("inline", "fasta"), required=False, cardinality="many"),
        "rna": PortSpec(RnaSequence, ("inline", "fasta"), required=False, cardinality="many"),
        "ligands": PortSpec(
            (Molecule, Molecules), ("smiles", "sdf", "mol2", "ccd"), required=False, cardinality="many",
        ),
        "templates": PortSpec(Structure, ("pdb", "cif"), required=False, cardinality="many"),
        "msas": PortSpec(File, ("a3m", "csv"), required=False, cardinality="many"),
        "constraints": PortSpec(File, "json", required=False),
    }
    outputs = {
        "predicted_structures": PortSpec(ComplexStructure, ("cif", "pdb"), cardinality="many"),
        "confidence_scores": PortSpec(Report, "json", cardinality="many"),
        "affinity_scores": PortSpec(Report, "json", required=False, cardinality="many"),
        "scores": PortSpec(Scores, "csv", description="Per-model confidence_score and affinity values"),
        "prediction_arrays": PortSpec(File, "npz", required=False, cardinality="many"),
        "run_summary": PortSpec(Report, "json"),
        "vina_receptor": PortSpec(
            ProteinStructure, "pdb", required=False,
            description="Receptor-only structure extracted from the selected Boltz complex",
        ),
        "docking_box": PortSpec(
            BindingSite, "box", required=False,
            description="Ligand-centered Vina search box extracted from Boltz coordinates",
        ),
    }
    resources = ResourceSpec(cpus=8, memory="32GB", gpus=1, time="12:00:00", partition="gpu")
    environment = EnvironmentSpec(executables=["boltz", "python"])
    config_fields = {
        "cache": ConfigField((str, type(None)), default=None),
        "checkpoint": ConfigField((str, type(None)), default=None),
        "devices": ConfigField(int, default=1, minimum=1),
        "accelerator": ConfigField(str, default="gpu", choices=("gpu", "cpu", "tpu")),
        "recycling_steps": ConfigField(int, default=3, minimum=0),
        "sampling_steps": ConfigField(int, default=200, minimum=1),
        "diffusion_samples": ConfigField(int, default=1, minimum=1),
        "max_parallel_samples": ConfigField(int, default=5, minimum=1),
        "step_scale": ConfigField((int, float), default=1.5, minimum=0),
        "output_format": ConfigField(str, default="mmcif", choices=("mmcif", "pdb")),
        "num_workers": ConfigField(int, default=2, minimum=0),
        "preprocessing_threads": ConfigField(
            (int, type(None)),
            default=None,
            validator=lambda value: (
                "must be >= 1 when set" if value is not None and value < 1 else None
            ),
        ),
        "method": ConfigField((str, type(None)), default=None),
        "affinity_binder": ConfigField((str, type(None)), default=None),
        "properties": ConfigField((list, type(None)), default=None),
        "constraints": ConfigField((list, type(None)), default=None),
        "use_msa_server": ConfigField(bool, default=True),
        "msa_server_url": ConfigField(str, default="https://api.colabfold.com"),
        "msa_pairing_strategy": ConfigField(str, default="greedy", choices=("greedy", "complete")),
        "single_sequence_mode": ConfigField(bool, default=False),
        "use_potentials": ConfigField(bool, default=False),
        "override": ConfigField(bool, default=True),
        "write_full_pae": ConfigField(bool, default=False),
        "write_full_pde": ConfigField(bool, default=False),
        "subsample_msa": ConfigField(bool, default=False),
        "max_msa_seqs": ConfigField(int, default=8192, minimum=1),
        "num_subsampled_msa": ConfigField(int, default=1024, minimum=1),
        "no_kernels": ConfigField(bool, default=False),
        "sampling_steps_affinity": ConfigField(int, default=200, minimum=1),
        "diffusion_samples_affinity": ConfigField(int, default=5, minimum=1),
        "affinity_mw_correction": ConfigField(bool, default=False),
        "affinity_checkpoint": ConfigField((str, type(None)), default=None),
        # Optional handoff for downstream docking methods.  Prediction-only
        # callers keep the historical output contract unless this is enabled.
        "emit_docking_inputs": ConfigField(bool, default=False),
        "docking_box_padding": ConfigField((int, float), default=8.0, minimum=0),
    }

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        """Entity content and optional software are checked on the execution host."""
        return []

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        """Check entity, ligand, MSA, and conditional handoff prerequisites."""
        errors: List[str] = []
        entity_names = ("proteins", "dna", "rna", "ligands")
        if not any(inputs.get(name) for name in entity_names):
            errors.append("Boltz requires at least one entity input")
        ligands = inputs.get("ligands") or []
        ligand_values = ligands if isinstance(ligands, list) else [ligands]
        for artifact in ligand_values:
            if not isinstance(artifact, Artifact):
                continue
            if artifact.format == "sdf":
                try:
                    from rdkit import Chem  # noqa: F401
                except ImportError:
                    errors.append("Boltz SDF ligand inputs require the RDKit package")
                    break
                if artifact.is_file and (not artifact.path.exists() or artifact.path.stat().st_size == 0):
                    errors.append(f"Boltz ligand input is missing or empty: {artifact.path}")
                else:
                    try:
                        records = artifact.records()
                        for record in records:
                            if Chem.MolFromMolBlock(record.block, sanitize=True, removeHs=False) is None:
                                errors.append(f"Boltz SDF ligand record {record.index} ('{record.id}') is invalid")
                        if not records:
                            errors.append("Boltz SDF ligand input has no molecule records")
                    except Exception as exc:
                        errors.append(f"Could not read Boltz SDF ligand records: {exc}")
        if self.config.get("affinity_binder") and not ligands:
            errors.append("affinity_binder requires at least one ligand input")
        # The translation validates exact chain-specific MSA coverage, supplied
        # constraints, ligand representations and usable entity content. It is
        # intentionally performed on the execution host before invoking Boltz.
        try:
            document = build_input_yaml(inputs, dict(self.config), ctx.work_dir / "boltz2_dependency_input.yaml")
            ligand_ids = {
                str(payload.get("id"))
                for item in document.get("sequences", [])
                for key, payload in item.items()
                if key == "ligand" and isinstance(payload, dict)
            }
            for property_index, item in enumerate(document.get("properties", [])):
                if not isinstance(item, dict) or "affinity" not in item:
                    continue
                affinity = item["affinity"]
                binder = affinity.get("binder") if isinstance(affinity, dict) else None
                if not isinstance(binder, str) or not binder:
                    errors.append(f"Affinity property {property_index} requires a ligand binder chain ID")
                elif binder not in ligand_ids:
                    errors.append(f"Affinity binder '{binder}' does not match a ligand chain ID")
        except Exception as exc:
            errors.append(f"Boltz input prerequisites: {exc}")
        if self.config.get("emit_docking_inputs"):
            if not inputs.get("proteins"):
                errors.append("emit_docking_inputs requires at least one protein input")
            if not ligands:
                errors.append("emit_docking_inputs requires at least one ligand input")
        return errors

    def _affinity_requested(self) -> bool:
        return bool(self.config.get("affinity_binder")) or any(
            isinstance(item, dict) and "affinity" in item for item in self.config.get("properties") or []
        )

    @staticmethod
    def _load_report(value: Any, errors: List[str], label: str) -> Any:
        if not isinstance(value, Artifact):
            errors.append(f"Missing {label} output")
            return None
        try:
            payload = value.load()
            if not isinstance(payload, dict):
                errors.append(f"{label} must contain a JSON object")
                return None
            return payload
        except Exception as exc:
            errors.append(f"Could not read {label}: {exc}")
            return None

    @staticmethod
    def _report_values(values: Any) -> List[float]:
        if isinstance(values, bool):
            return []
        if isinstance(values, (int, float)):
            try:
                number = float(values)
            except (TypeError, ValueError):
                return []
            return [number] if math.isfinite(number) else []
        if isinstance(values, dict):
            numbers: List[float] = []
            for value in values.values():
                numbers.extend(Boltz2Prediction._report_values(value))
            return numbers
        if isinstance(values, (tuple, list)):
            numbers: List[float] = []
            for value in values:
                numbers.extend(Boltz2Prediction._report_values(value))
            return numbers
        return []

    @staticmethod
    def _has_nonfinite(value: Any) -> bool:
        if isinstance(value, float):
            return not math.isfinite(value)
        if isinstance(value, dict):
            return any(Boltz2Prediction._has_nonfinite(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(Boltz2Prediction._has_nonfinite(item) for item in value)
        return False

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Validate the complete Boltz structure/report/score pool."""
        errors: List[str] = []
        structures_value = outputs.get("predicted_structures")
        structures = structures_value if isinstance(structures_value, list) else (
            [structures_value] if structures_value is not None else []
        )
        if not structures:
            errors.append("Boltz produced no predicted structures")
        structure_stems: List[str] = []
        for index, structure in enumerate(structures):
            if not isinstance(structure, Artifact):
                errors.append(f"Predicted structure {index} is not an artifact")
                continue
            if not structure.is_file:
                errors.append(f"Predicted structure {index} is not file-backed")
                continue
            if not structure.path.exists() or structure.path.stat().st_size == 0:
                errors.append(f"Predicted structure file is missing or empty: {structure.path}")
            expected_format = "cif" if self.config.get("output_format") == "mmcif" else self.config.get("output_format")
            if structure.format not in (expected_format, "mmcif" if expected_format == "cif" else expected_format):
                errors.append(
                    f"Predicted structure {structure.path} has format '{structure.format}', "
                    f"expected '{expected_format}'"
                )
            structure_stems.append(structure.path.stem)
            errors.extend(structure_errors(structure.path, structure.format, "Boltz predicted structure"))
        if len(structure_stems) != len(set(structure_stems)):
            errors.append("Boltz predicted structure stems are duplicated")
        expected_samples = int(self.config.get("diffusion_samples", 1))
        if len(structures) != expected_samples:
            errors.append(
                f"Boltz produced {len(structures)} predicted structures; expected {expected_samples} "
                "from diffusion_samples"
            )

        summary = self._load_report(outputs.get("run_summary"), errors, "Boltz run_summary")
        if not isinstance(summary, dict):
            summary = {}
        if summary.get("status") != "completed":
            errors.append(f"Boltz run summary status is not completed: {summary.get('status')!r}")
        summary_structures = summary.get("structures")
        if isinstance(summary_structures, list) and sorted(str(value) for value in summary_structures) != sorted(str(value.path) for value in structures if isinstance(value, Artifact) and value.is_file):
            errors.append("Boltz run summary structure inventory does not match collected structures")
        elif not isinstance(summary_structures, list):
            errors.append("Boltz run summary is missing its structures inventory")

        confidence_value = outputs.get("confidence_scores")
        confidence = confidence_value if isinstance(confidence_value, list) else (
            [confidence_value] if confidence_value is not None else []
        )
        confidence_paths: List[Path] = []
        for index, report in enumerate(confidence):
            payload = self._load_report(report, errors, f"Boltz confidence report {index}")
            if isinstance(report, Artifact) and report.is_file:
                confidence_paths.append(report.path)
            if payload is not None and not self._report_values(payload):
                errors.append(f"Boltz confidence report {index} has no finite numeric values")
            if self._has_nonfinite(payload):
                errors.append(f"Boltz confidence report {index} contains non-finite values")
        if len(confidence_paths) > 1:
            for report in confidence_paths:
                stem = report.stem.lower()
                if stem.startswith("confidence_"):
                    stem = stem[len("confidence_"):]
                if not any(stem in model.lower() or model.lower() in stem for model in structure_stems):
                    errors.append(f"Unexpected confidence report '{report.name}' does not match a predicted model")
        if structure_stems:
            for stem in structure_stems:
                matched = any(
                    report.stem.lower().replace("confidence_", "", 1) in stem.lower()
                    or stem.lower() in report.stem.lower()
                    for report in confidence_paths
                )
                if not matched and len(confidence_paths) != 1:
                    errors.append(f"No confidence report matches predicted structure '{stem}'")
                elif confidence_paths:
                    structure_path = next((item.path for item in structures if isinstance(item, Artifact) and item.is_file and item.path.stem == stem), None)
                    if structure_path is not None and _matching_confidence(structure_path, confidence_paths) is None:
                        errors.append(f"Confidence report for structure '{stem}' has no usable score")
        if len(confidence_paths) != len(set(confidence_paths)):
            errors.append("Boltz confidence report inventory contains duplicates")

        scores_value = outputs.get("scores")
        if not isinstance(scores_value, Artifact):
            errors.append("Missing Boltz scores output")
            score_rows: List[Dict[str, Any]] = []
        else:
            try:
                score_rows, row_errors = scores_value.rows_with_errors()
                errors.extend(f"Could not read Boltz scores: {error}" for error in row_errors)
            except Exception as exc:
                errors.append(f"Could not read Boltz scores: {exc}")
                score_rows = []
        if len(score_rows) != len(structure_stems):
            errors.append(
                f"Boltz score row count {len(score_rows)} does not match structure count {len(structure_stems)}"
            )
        seen_ids: set[str] = set()
        for index, row in enumerate(score_rows):
            row_id = str(row.get("id", "")).strip()
            if row_id in seen_ids:
                errors.append(f"Boltz score row {index} duplicates model ID '{row_id}'")
            seen_ids.add(row_id)
            if row_id not in structure_stems:
                errors.append(f"Boltz score row {index} references unknown model '{row_id}'")
            for column in ("confidence_score", "affinity_pred_value", "affinity_probability_binary"):
                if column in row or column == "confidence_score":
                    try:
                        number = float(row.get(column)) if not isinstance(row.get(column), bool) else math.nan
                    except (TypeError, ValueError):
                        number = math.nan
                    if not math.isfinite(number):
                        errors.append(f"Boltz score row {index} has invalid {column}")
                    elif column == "confidence_score" and row_id in structure_stems:
                        expected_confidence = _matching_confidence(
                            next(item.path for item in structures if isinstance(item, Artifact) and item.is_file and item.path.stem == row_id),
                            confidence_paths,
                        )
                        if expected_confidence is not None and not math.isclose(number, expected_confidence, rel_tol=1e-9, abs_tol=1e-9):
                            errors.append(f"Boltz score row {index} confidence_score disagrees with its confidence report")
        missing_ids = sorted(set(structure_stems) - seen_ids)
        if missing_ids:
            errors.append(f"Boltz scores are missing model IDs: {missing_ids}")

        affinity = outputs.get("affinity_scores")
        if self._affinity_requested():
            affinity_items = affinity if isinstance(affinity, list) else ([affinity] if affinity is not None else [])
            if not affinity_items:
                errors.append("Affinity prediction was requested but no affinity report was produced")
            for index, report in enumerate(affinity_items):
                payload = self._load_report(report, errors, f"Boltz affinity report {index}")
                if isinstance(payload, dict) and not any(
                    key.startswith("affinity") and self._report_values(value)
                    for key, value in payload.items()
                ):
                    errors.append(f"Boltz affinity report {index} has no affinity values")
                if self._has_nonfinite(payload):
                    errors.append(f"Boltz affinity report {index} contains non-finite values")
        elif affinity is not None:
            affinity_items = affinity if isinstance(affinity, list) else [affinity]
            for index, report in enumerate(affinity_items):
                payload = self._load_report(report, errors, f"Boltz optional affinity report {index}")
                if self._has_nonfinite(payload):
                    errors.append(f"Boltz optional affinity report {index} contains non-finite values")

        arrays = outputs.get("prediction_arrays") or []
        arrays = arrays if isinstance(arrays, list) else [arrays]
        for index, array in enumerate(arrays):
            if not isinstance(array, Artifact) or not array.is_file:
                errors.append(f"Boltz prediction array {index} is not file-backed")
            else:
                try:
                    with zipfile.ZipFile(array.path) as handle:
                        if not handle.namelist() or any(not name.endswith(".npy") for name in handle.namelist()):
                            errors.append(f"Boltz prediction array {index} is not a usable NPZ archive")
                        for name in handle.namelist():
                            with handle.open(name) as entry:
                                if entry.read(6) != b"\x93NUMPY":
                                    errors.append(f"Boltz prediction array {index} entry '{name}' has no NumPy header")
                        corrupt = handle.testzip()
                        if corrupt:
                            errors.append(f"Boltz prediction array {index} contains corrupt entry '{corrupt}'")
                except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                    errors.append(f"Could not read Boltz prediction array {index}: {exc}")

        if self.config.get("emit_docking_inputs"):
            receptor = outputs.get("vina_receptor")
            if not isinstance(receptor, Artifact) or not receptor.is_file:
                errors.append("Boltz docking handoff is missing vina_receptor")
            elif not receptor.path.exists() or receptor.path.stat().st_size == 0:
                errors.append("Boltz docking receptor is missing or empty")
            else:
                errors.extend(structure_errors(receptor.path, receptor.format, "Boltz docking receptor"))
            box = outputs.get("docking_box")
            if not isinstance(box, Artifact) or not isinstance(box.value, dict):
                errors.append("Boltz docking handoff is missing docking_box")
            else:
                size = box.value.get("size")
                center = box.value.get("center")
                if not self._valid_vector(size, positive=True):
                    errors.append("Boltz docking_box size must contain three positive values")
                if not self._valid_vector(center):
                    errors.append("Boltz docking_box center must contain three values")
        return errors

    @staticmethod
    def _valid_vector(value: Any, positive: bool = False) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return False
        if any(isinstance(item, bool) for item in value):
            return False
        try:
            numbers = [float(item) for item in value]
        except (TypeError, ValueError):
            return False
        return all(math.isfinite(item) and (not positive or item > 0) for item in numbers)

    def validate_environment(self) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not check_executable("boltz"):
            errors.append("Boltz CLI (boltz) not found in PATH; install boltz[cuda] or boltz")
        if not check_executable("python"):
            errors.append("Python not found in PATH")
        if self.config.get("emit_docking_inputs"):
            try:
                import gemmi  # noqa: F401
            except ImportError:
                errors.append(
                    "Gemmi is required when emit_docking_inputs is enabled; install gemmi"
                )
        return not errors, errors

    def prepare(self, task: Task) -> None:
        work_dir = Path(task.work_dir or (Path.cwd() / "runs" / task.task_id))
        work_dir.mkdir(parents=True, exist_ok=True)
        task.work_dir = work_dir
        input_yaml = work_dir / "boltz2_input.yaml"
        config = dict(self.config)
        document = build_input_yaml(task.inputs, config, input_yaml)
        output_dir = work_dir / "boltz2_output"
        entity_mapping = {"polymer_ids": [], "ligand_ids": []}
        for entity in document.get("sequences", []):
            for entity_type, payload in entity.items():
                if not isinstance(payload, dict):
                    continue
                ids = payload.get("id", [])
                ids = ids if isinstance(ids, (list, tuple)) else [ids]
                target = "ligand_ids" if entity_type == "ligand" else "polymer_ids"
                entity_mapping[target].extend(str(item) for item in ids if item)
        manifest = {
            "manifest_version": 1,
            "work_dir": str(work_dir),
            "input_yaml": str(input_yaml),
            "output_dir": str(output_dir),
            "sample_name": input_yaml.stem,
            "config": config,
            "entity_mapping": entity_mapping,
        }
        manifest_path = work_dir / "boltz2_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        task.intermediate_outputs = task.intermediate_outputs or {}
        task.intermediate_outputs["boltz2_manifest"] = str(manifest_path)
        task.intermediate_outputs["boltz2_input_yaml"] = str(input_yaml)

    def get_command(self, task: Task) -> str:
        """Use the common scheduler runner; :meth:`run` still invokes boltz2_runner."""
        return super().get_command(task)

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        self.prepare(ctx.task)
        try:
            run_manifest(Path(ctx.task.intermediate_outputs["boltz2_manifest"]))
        except Exception as exc:
            ctx.output_errors.append(f"Boltz execution failed: {exc}")
        return self._collect(ctx.work_dir, ctx.output_errors)

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        return super().collect_outputs(task)

    def _collect(self, work_dir: Path, output_errors: Optional[List[str]] = None) -> Dict[str, Any]:
        """Gather independent outputs before allowing the pooled validator to decide."""
        summary_path = Path(work_dir) / "outputs" / "boltz2_run.json"
        output_errors = output_errors if output_errors is not None else []
        summary: Dict[str, Any] = {}
        try:
            loaded = json.loads(summary_path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError("expected a JSON object")
            summary = loaded
        except Exception as exc:
            output_errors.append(f"Could not read Boltz run summary: {exc}")

        def paths_for(key: str, patterns: Tuple[str, ...]) -> List[Path]:
            paths: List[Path] = []
            values = summary.get(key, [])
            if not isinstance(values, list):
                output_errors.append(f"Boltz run summary '{key}' must be a list")
                values = []
            for index, value in enumerate(values):
                if not isinstance(value, (str, Path)):
                    output_errors.append(f"Boltz run summary '{key}' item {index} is not a path")
                else:
                    paths.append(Path(value))
            # Retain independently discoverable files even when the summary
            # is malformed or omits one sibling result.
            prediction_root = Path(work_dir) / "boltz2_output"
            discovered = sorted({path for pattern in patterns for path in prediction_root.glob(pattern)})
            for path in discovered:
                if path not in paths:
                    paths.append(path)
            return paths

        structures = paths_for("structures", ("**/*_model_*.cif", "**/*_model_*.mmcif", "**/*_model_*.pdb"))
        confidence = paths_for("confidence", ("**/confidence_*.json",))
        affinity = paths_for("affinity", ("**/affinity_*.json",))
        arrays = paths_for("arrays", ("**/*.npz",))
        meta = {"prediction_dir": summary.get("prediction_dir"), "model": "boltz2"}
        outputs: Dict[str, Any] = {
            "predicted_structures": [
                ComplexStructure.from_file(path, "pdb" if path.suffix == ".pdb" else "cif", meta)
                for path in structures
            ],
            "confidence_scores": [Report.from_file(path, "json", meta) for path in confidence],
            "run_summary": Report.from_file(summary_path, "json", meta),
        }
        try:
            outputs["scores"] = Scores.from_records(
                self._score_rows(structures, confidence, affinity, output_errors),
                Path(work_dir) / "outputs" / "boltz2_scores.csv",
                directions=BOLTZ_SCORE_DIRECTIONS, metadata=meta,
            )
        except Exception as exc:
            output_errors.append(f"Could not create Boltz score table: {exc}")
        if affinity:
            outputs["affinity_scores"] = [Report.from_file(path, "json", meta) for path in affinity]
        if arrays:
            outputs["prediction_arrays"] = [File.from_file(path, "npz", meta) for path in arrays]
        if self.config.get("emit_docking_inputs"):
            receptor_path = summary.get("docking_receptor") or Path(work_dir) / "outputs" / "vina_receptor.pdb"
            docking_box = summary.get("docking_box")
            try:
                outputs["vina_receptor"] = ProteinStructure.from_file(Path(receptor_path), "pdb", meta)
            except Exception as exc:
                output_errors.append(f"Could not collect Boltz docking receptor: {exc}")
            if docking_box is not None:
                outputs["docking_box"] = BindingSite.inline(docking_box, "box", meta)
        return outputs

    @staticmethod
    def _score_rows(
        structures: List[Path], confidence: List[Path], affinity: List[Path],
        output_errors: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """One row per model: its confidence score plus the sample's affinity values."""
        affinity_values: Dict[str, Any] = {}
        output_errors = output_errors if output_errors is not None else []
        for index, report in enumerate(affinity):
            try:
                payload = json.loads(report.read_text())
                if not isinstance(payload, dict):
                    raise ValueError("expected a JSON object")
                if index == 0:
                    affinity_values = {
                        key: value for key, value in payload.items()
                        if key.startswith("affinity") and isinstance(value, (int, float)) and not isinstance(value, bool)
                    }
            except Exception as exc:
                output_errors.append(f"Could not derive Boltz scores from affinity report {report}: {exc}")
        return [
            {"id": path.stem, "confidence_score": _matching_confidence(path, confidence), **affinity_values}
            for path in structures
        ]


# Short import alias for callers that refer to the method simply as Boltz2.
Boltz2 = Boltz2Prediction


class Boltz2PredictionMethod(Boltz2Prediction):
    """Compatibility registration using the descriptive method type name."""

    register_name = "boltz2_prediction"
