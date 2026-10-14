"""Boltz-2 biomolecular complex and affinity prediction method."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...core.task import Task
from ...data import (
    BindingSite, ComplexStructure, DnaSequence, File, Molecule, Molecules, ProteinSequence,
    ProteinStructure, Report, RnaSequence, Scores, Structure,
)
from ..utils import check_executable
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
        errors: List[str] = []
        if not any(inputs.get(name) for name in ("proteins", "dna", "rna", "ligands")):
            errors.append("Boltz requires at least one entity input")
        if any(item.format == "sdf" for item in inputs.get("ligands") or []):
            try:
                import rdkit  # noqa: F401
            except ImportError:
                errors.append("Boltz SDF ligand inputs require the RDKit package")
        if self.config.get("affinity_binder") and not inputs.get("ligands"):
            errors.append("affinity_binder requires at least one ligand input")
        if self.config.get("emit_docking_inputs"):
            if not inputs.get("proteins"):
                errors.append("emit_docking_inputs requires at least one protein input")
            if not inputs.get("ligands"):
                errors.append("emit_docking_inputs requires at least one ligand input")
        return errors

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
        """Command submitted to SLURM: the Boltz runner on this task's manifest."""
        ctx = RunContext.for_task(task)
        task.inputs = self.format_inputs(task.inputs, ctx.input_dir)
        self.prepare(task)
        manifest_path = Path(task.intermediate_outputs["boltz2_manifest"]).resolve()
        return shlex.join([
            sys.executable, "-m", "rapier.methods.prediction.boltz2_runner", "--manifest", str(manifest_path),
        ])

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        self.prepare(ctx.task)
        run_manifest(Path(ctx.task.intermediate_outputs["boltz2_manifest"]))
        return self._collect(ctx.work_dir)

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        ctx = RunContext.for_task(task)
        return self.format_outputs(self._collect(ctx.work_dir), ctx)

    def _collect(self, work_dir: Path) -> Dict[str, Any]:
        """Typed outputs from the runner's ``boltz2_run.json`` summary."""
        summary_path = Path(work_dir) / "outputs" / "boltz2_run.json"
        if not summary_path.exists():
            raise FileNotFoundError("Boltz run summary not found")
        summary = json.loads(summary_path.read_text())
        structures = [Path(path) for path in summary.get("structures", [])]
        confidence = [Path(path) for path in summary.get("confidence", [])]
        affinity = [Path(path) for path in summary.get("affinity", [])]
        arrays = [Path(path) for path in summary.get("arrays", [])]
        for path in structures + confidence + affinity + arrays:
            if not path.exists():
                raise FileNotFoundError(f"Boltz output file not found: {path}")
        meta = {"prediction_dir": summary.get("prediction_dir"), "model": "boltz2"}
        outputs: Dict[str, Any] = {
            "predicted_structures": [
                ComplexStructure.from_file(path, "pdb" if path.suffix == ".pdb" else "cif", meta)
                for path in structures
            ],
            "confidence_scores": [Report.from_file(path, "json", meta) for path in confidence],
            "scores": Scores.from_records(
                self._score_rows(structures, confidence, affinity),
                Path(work_dir) / "outputs" / "boltz2_scores.csv",
                directions=BOLTZ_SCORE_DIRECTIONS, metadata=meta,
            ),
            "run_summary": Report.from_file(summary_path, "json", meta),
        }
        if affinity:
            outputs["affinity_scores"] = [Report.from_file(path, "json", meta) for path in affinity]
        if arrays:
            outputs["prediction_arrays"] = [File.from_file(path, "npz", meta) for path in arrays]
        if self.config.get("emit_docking_inputs"):
            receptor_path = summary.get("docking_receptor")
            docking_box = summary.get("docking_box")
            if not receptor_path or not docking_box:
                raise FileNotFoundError(
                    "Boltz did not produce docking inputs; expected a receptor and docking box"
                )
            receptor = Path(receptor_path)
            if not receptor.exists():
                raise FileNotFoundError(f"Boltz receptor output not found: {receptor}")
            outputs["vina_receptor"] = ProteinStructure.from_file(receptor, "pdb", meta)
            outputs["docking_box"] = BindingSite.inline(docking_box, "box", meta)
        return outputs

    @staticmethod
    def _score_rows(structures: List[Path], confidence: List[Path], affinity: List[Path]) -> List[Dict[str, Any]]:
        """One row per model: its confidence score plus the sample's affinity values."""
        affinity_values: Dict[str, Any] = {}
        if affinity:
            payload = json.loads(affinity[0].read_text())
            affinity_values = {
                key: value for key, value in payload.items()
                if key.startswith("affinity") and isinstance(value, (int, float))
            }
        return [
            {"id": path.stem, "confidence_score": _matching_confidence(path, confidence), **affinity_values}
            for path in structures
        ]


# Short import alias for callers that refer to the method simply as Boltz2.
Boltz2 = Boltz2Prediction


class Boltz2PredictionMethod(Boltz2Prediction):
    """Compatibility registration using the descriptive method type name."""

    register_name = "boltz2_prediction"
