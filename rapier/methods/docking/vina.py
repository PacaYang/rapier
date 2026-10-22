"""AutoDock Vina docking method."""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...artifacts import Artifact
from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...core.task import Task
from ...data import BindingSite, Molecules, Poses, ProteinStructure, Scores
from ..base_implementations import ParallelizableMethod
from ..utils import DOCKING_SCORE_COLUMNS, check_executable, resolve_box, validate_pdb_file, validate_sdf_file
from .vina_runner import _ligand_names, run_manifest


class VinaDocking(Method, ParallelizableMethod):
    """Molecular docking using AutoDock Vina.

    The work is done by ``vina_runner``, driven by a JSON manifest written for
    each task, so local and SLURM execution run the same steps: receptors and
    ligands are prepared with Meeko, Vina docks each ligand separately (one
    failure does not stop the rest) and poses are exported back to SDF.

    Outputs: ``docked_poses`` (Poses titled by ligand id) and ``docking_scores``
    (Scores keyed by ``id``/``pose`` with ``affinity_kcal_mol``, lower is better).

    Parameters:
        - box_center / box_size: Deprecated fallback when no binding_site input is bound
        - exhaustiveness: Search exhaustiveness (default: 8, range: 1-32)
        - num_modes: Number of binding modes to generate (default: 9)
        - energy_range: Maximum energy difference for modes (kcal/mol, default: 3)
        - scoring: Vina scoring function (``vina`` or ``vinardo``)
        - cpu: Vina worker count; omitted uses the task's allocated CPUs
        - seed: Optional random seed for reproducible docking
    """

    register_name = "vina_docking"
    display_name = "AutoDock Vina"
    category = "docking"
    version = "1.2.5"
    inputs = {
        "receptor": PortSpec(ProteinStructure, ("pdb", "cif", "pdbqt")),
        "ligands": PortSpec(Molecules, "sdf"),
        "binding_site": PortSpec(BindingSite, "box", required=False, description="Docking search box"),
    }
    outputs = {
        "docked_poses": PortSpec(Poses, "sdf", description="Docked poses, titled by ligand id"),
        "docking_scores": PortSpec(Scores, "csv", description="id, pose, affinity_kcal_mol, rmsd_lb, rmsd_ub"),
    }
    config_fields = {
        # Deprecated: bind a binding_site input instead.
        "box_center": ConfigField(list, default=[0.0, 0.0, 0.0]),
        "box_size": ConfigField(list, default=[20.0, 20.0, 20.0]),
        "exhaustiveness": ConfigField(int, default=8, minimum=1, maximum=32),
        "num_modes": ConfigField(int, default=9, minimum=1),
        "energy_range": ConfigField((int, float), default=3, minimum=0),
        "scoring": ConfigField(str, default="vina", choices=("vina", "vinardo")),
        "cpu": ConfigField((int, type(None)), default=None),
        "seed": ConfigField((int, type(None)), default=None),
        "min_rmsd": ConfigField((int, float, type(None)), default=None),
        "verbosity": ConfigField(int, default=1, minimum=0, maximum=2),
        "allow_partial_failures": ConfigField(bool, default=True),
    }
    resources = ResourceSpec(cpus=4, memory="8GB", time="02:00:00", partition="cpu")
    environment = EnvironmentSpec(
        executables=["vina", "mk_prepare_receptor.py", "mk_prepare_ligand.py", "mk_export.py", "python"],
        python_packages=["prody"],
    )

    def validate_environment(self) -> Tuple[bool, List[str]]:
        required_tools = (
            ("vina", "AutoDock Vina (vina)"),
            ("mk_prepare_receptor.py", "Meeko receptor preparation (mk_prepare_receptor.py)"),
            ("mk_prepare_ligand.py", "Meeko ligand preparation (mk_prepare_ligand.py)"),
            ("mk_export.py", "Meeko pose export (mk_export.py)"),
            ("python", "Python"),
        )
        errors = [f"{label} not found in PATH" for executable, label in required_tools if not check_executable(executable)]
        # Meeko's receptor preparation imports ProDy at runtime.
        try:
            import prody  # noqa: F401
        except ImportError:
            errors.append("ProDy Python package not found (required by Meeko receptor preparation)")
        return not errors, errors

    def _box(self, inputs: Dict[str, Any]) -> Dict[str, List[float]]:
        return resolve_box(inputs.get("binding_site"), self.config["box_center"], self.config["box_size"])

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        """Input content prerequisites are checked on the execution host."""
        return []

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        """Check the staged ligand inventory and docking box on the execution host."""
        errors: List[str] = []
        receptor = inputs.get("receptor")
        if isinstance(receptor, Artifact) and receptor.is_file:
            if receptor.format in ("pdb", "pdbqt"):
                try:
                    valid, message = validate_pdb_file(receptor.path)
                    if not valid:
                        errors.append(message)
                except Exception as exc:
                    errors.append(f"Could not validate Vina receptor: {exc}")
            if receptor.format in ("cif", "mmcif"):
                try:
                    import gemmi
                except ImportError:
                    errors.append("CIF receptor input requires the optional Gemmi package")
                else:
                    try:
                        structure = gemmi.read_structure(str(receptor.path))
                        atoms = [atom for model in structure for chain in model for residue in chain for atom in residue]
                        if not atoms:
                            errors.append("Vina CIF receptor has no readable atom coordinates")
                        elif any(not all(math.isfinite(value) for value in (atom.pos.x, atom.pos.y, atom.pos.z)) for atom in atoms):
                            errors.append("Vina CIF receptor contains non-finite atom coordinates")
                    except Exception as exc:
                        errors.append(f"Could not read Vina CIF receptor: {exc}")
            else:
                try:
                    atoms = [line for line in receptor.path.read_text().splitlines() if line.startswith(("ATOM  ", "HETATM"))]
                    for atom_index, line in enumerate(atoms):
                        try:
                            coordinates = [float(line[start:start + 8]) for start in (30, 38, 46)]
                            valid = all(math.isfinite(value) for value in coordinates)
                        except ValueError:
                            valid = False
                        if not valid:
                            errors.append(f"Vina receptor atom {atom_index} has invalid coordinates")
                    if not atoms:
                        errors.append("Vina receptor has no atom records")
                except Exception as exc:
                    errors.append(f"Could not read Vina receptor: {exc}")
        else:
            errors.append("Vina requires a normalized file-backed receptor")
        ligands = inputs.get("ligands")
        if not isinstance(ligands, Artifact):
            errors.append("Vina requires a normalized ligand collection")
        else:
            try:
                valid, message = validate_sdf_file(ligands.path)
                if not valid:
                    errors.append(message)
            except Exception as exc:
                errors.append(f"Could not validate Vina ligand file: {exc}")
            try:
                if not ligands.records():
                    errors.append("Vina requires at least one SDF ligand record")
            except Exception as exc:
                errors.append(f"Could not read Vina ligand inventory: {exc}")
        try:
            self._box(inputs)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
        return errors

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Validate Vina's accounted ligand outcomes and pooled pose/score rows."""
        errors: List[str] = []
        expected_records = []
        try:
            expected_records = inputs["ligands"].records()
        except Exception as exc:
            errors.append(f"Could not read Vina input inventory during output validation: {exc}")
        expected_by_index: Dict[int, str] = {}
        for record, name in zip(expected_records, _ligand_names([record.block or "" for record in expected_records])):
            expected_by_index[int(record.index)] = name

        poses = outputs.get("docked_poses")
        scores = outputs.get("docking_scores")
        if not isinstance(poses, Artifact):
            errors.append("Missing docked_poses output")
        if not isinstance(scores, Artifact):
            errors.append("Missing docking_scores output")
        metadata = {}
        for artifact in (poses, scores):
            if isinstance(artifact, Artifact) and artifact.metadata.get("run_summary_data"):
                metadata = artifact.metadata
                break
        if isinstance(poses, Artifact) and isinstance(scores, Artifact):
            for key in ("run_summary_data", "failed_ligands", "successful_ligands"):
                if poses.metadata.get(key) != scores.metadata.get(key):
                    errors.append(f"Vina pose and score metadata disagree on '{key}'")
        summary = metadata.get("run_summary_data")
        if not isinstance(summary, dict):
            errors.append("Vina outputs are missing a valid run summary")
            summary = {}
        if summary.get("status") != "completed":
            errors.append(f"Vina run summary status is not completed: {summary.get('status')!r}")
        outcomes = summary.get("ligands")
        if not isinstance(outcomes, list):
            errors.append("Vina run summary is missing per-ligand outcomes")
            outcomes = []
        outcome_indexes: List[int] = []
        success_ids: List[str] = []
        failed_ids: List[str] = []
        summary_score_keys: List[Tuple[str, int]] = []
        for position, outcome in enumerate(outcomes):
            if not isinstance(outcome, dict):
                errors.append(f"Vina ligand outcome {position} is malformed")
                continue
            index = outcome.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                errors.append(f"Vina ligand outcome {position} has no valid input index")
                index = None
            else:
                outcome_indexes.append(index)
            ligand_id = str(outcome.get("ligand", ""))
            expected_id = expected_by_index.get(index)
            if expected_id is None and index is not None:
                errors.append(f"Vina outcome references unexpected input index {index}")
            elif expected_id is not None and ligand_id != expected_id:
                errors.append(
                    f"Vina outcome at input index {index} has ID '{ligand_id}', expected '{expected_id}'"
                )
            if outcome.get("success") is True:
                success_ids.append(ligand_id)
                if not isinstance(outcome.get("scores"), list) or not outcome.get("scores"):
                    errors.append(f"Successful ligand '{ligand_id}' has no summary scores")
                else:
                    for score_index, score in enumerate(outcome["scores"]):
                        if not isinstance(score, dict):
                            errors.append(f"Vina summary score {score_index} for '{ligand_id}' is malformed")
                            continue
                        try:
                            mode = int(str(score.get("mode")))
                        except (TypeError, ValueError):
                            errors.append(f"Vina summary score {score_index} for '{ligand_id}' has invalid mode")
                        else:
                            summary_score_keys.append((ligand_id, mode))
                        for column in ("affinity", "rmsd_lb", "rmsd_ub"):
                            value = score.get(column)
                            try:
                                valid_number = not isinstance(value, bool) and math.isfinite(float(value))
                            except (TypeError, ValueError):
                                valid_number = False
                            if not valid_number:
                                errors.append(f"Vina summary score {score_index} for '{ligand_id}' has invalid {column}")
            elif outcome.get("success") is False:
                failed_ids.append(ligand_id)
                if not str(outcome.get("error", "")).strip():
                    errors.append(f"Failed ligand '{ligand_id}' has no failure reason")
            else:
                errors.append(f"Vina ligand outcome for '{ligand_id}' has no boolean success value")
        if len(outcome_indexes) != len(set(outcome_indexes)):
            errors.append("Vina summary contains duplicate input ligand indexes")
        missing_indexes = sorted(set(expected_by_index) - set(outcome_indexes))
        if missing_indexes:
            errors.append(f"Vina did not account for input ligand indexes: {missing_indexes}")
        extra_indexes = sorted(set(outcome_indexes) - set(expected_by_index))
        if extra_indexes:
            errors.append(f"Vina summary has unexpected input indexes: {extra_indexes}")
        successful_count = summary.get("successful_ligands", -1)
        if not isinstance(successful_count, int) or isinstance(successful_count, bool):
            errors.append("Vina successful_ligands must be an integer")
            successful_count = -1
        if successful_count != len(success_ids):
            errors.append("Vina successful_ligands count does not match the outcome inventory")
        listed_failures = summary.get("failed_ligands")
        if not isinstance(listed_failures, list):
            errors.append("Vina summary failed_ligands must be a list")
        elif sorted(str(item) for item in listed_failures) != sorted(failed_ids):
            errors.append("Vina failed_ligands list does not match per-ligand outcomes")
        if not success_ids:
            errors.append("Vina produced no successful ligand poses")
        if failed_ids and not self.config.get("allow_partial_failures", True):
            errors.append(
                "Vina ligand failures are not allowed by allow_partial_failures=false: "
                + ", ".join(failed_ids)
            )

        if isinstance(poses, Artifact) or isinstance(scores, Artifact):
            pose_keys = []
            rows = []
            try:
                if isinstance(poses, Artifact):
                    pose_records = poses.records()
                    pose_keys = [(record.id, record.pose) for record in pose_records]
                    for record in pose_records:
                        if not record.heavy_atoms:
                            errors.append(f"Vina pose ({record.id}, {record.pose}) has no readable heavy-atom coordinates")
                        elif any(not all(math.isfinite(value) for value in xyz) for xyz in record.heavy_atoms):
                            errors.append(f"Vina pose ({record.id}, {record.pose}) has non-finite coordinates")
            except Exception as exc:
                errors.append(f"Could not read Vina pose file: {exc}")
            try:
                if isinstance(scores, Artifact):
                    rows, row_errors = scores.rows_with_errors()
                    errors.extend(f"Could not read Vina score table: {error}" for error in row_errors)
            except Exception as exc:
                errors.append(f"Could not read Vina score table: {exc}")
            score_keys: List[Tuple[str, int]] = []
            for row_index, row in enumerate(rows):
                ligand_id = str(row.get("id", row.get("ligand", ""))).strip()
                try:
                    pose = int(str(row.get("pose", row.get("mode"))))
                except (TypeError, ValueError):
                    errors.append(f"Vina score row {row_index} has no valid pose number")
                    pose = None
                if pose is not None:
                    score_keys.append((ligand_id, pose))
                if ligand_id not in success_ids:
                    errors.append(f"Vina score row {row_index} references non-successful ligand '{ligand_id}'")
                if pose is not None and pose < 1:
                    errors.append(f"Vina score row {row_index} has non-positive pose number {pose}")
                for column in ("affinity_kcal_mol", "rmsd_lb", "rmsd_ub"):
                    try:
                        value = float(row.get(column)) if not isinstance(row.get(column), bool) else math.nan
                    except (TypeError, ValueError):
                        value = math.nan
                    if not math.isfinite(value):
                        errors.append(f"Vina score row {row_index} has invalid {column}")
            if len(pose_keys) != len(set(pose_keys)):
                errors.append("Vina pose inventory contains duplicate (id, pose) keys")
            if len(score_keys) != len(set(score_keys)):
                errors.append("Vina score inventory contains duplicate (id, pose) keys")
            if isinstance(scores, Artifact) and sorted(score_keys) != sorted(summary_score_keys):
                errors.append("Vina score inventory does not match per-ligand summary scores")
            if isinstance(poses, Artifact) and isinstance(scores, Artifact) and set(pose_keys) != set(score_keys):
                errors.append(
                    "Vina pose and score inventories differ "
                    f"(poses-only={sorted(set(pose_keys) - set(score_keys))}, "
                    f"scores-only={sorted(set(score_keys) - set(pose_keys))})"
                )
            represented = {ligand_id for ligand_id, _ in score_keys}
            missing_successes = sorted(set(success_ids) - represented)
            if missing_successes:
                errors.append(f"Vina successful ligands have no score/pose rows: {missing_successes}")
        return errors

    def _write_manifest(self, inputs: Dict[str, Any], work_dir: Path, resources: Dict[str, Any]) -> Path:
        """The deterministic manifest consumed by :mod:`vina_runner`."""
        work_dir.mkdir(parents=True, exist_ok=True)
        config = dict(self.config)
        if config.get("cpu") is None:
            config["cpu"] = resources.get("cpus", self.resource_spec.cpus)
        manifest = {
            "manifest_version": 1,
            "work_dir": str(work_dir),
            "receptor": str(inputs["receptor"].path),
            "ligands": str(inputs["ligands"].path),
            "box": self._box(inputs),
            "config": config,
        }
        path = work_dir / "vina_manifest.json"
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        return path

    def get_command(self, task: Task) -> str:
        """Use the common scheduler runner; :meth:`run` still invokes vina_runner."""
        return super().get_command(task)

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        manifest = self._write_manifest(inputs, ctx.work_dir, ctx.resources)
        ctx.task.intermediate_outputs["vina_manifest"] = str(manifest)
        try:
            run_manifest(manifest)
        except Exception as exc:
            ctx.output_errors.append(f"Vina execution failed: {exc}")
        return self._collect(ctx.output_dir, ctx.output_errors)

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        """Read the receipt produced by the common runner."""
        return super().collect_outputs(task)

    @staticmethod
    def _collect(output_dir: Path, output_errors: Optional[List[str]] = None) -> Dict[str, Any]:
        output_errors = output_errors if output_errors is not None else []
        poses = output_dir / "docked_poses.sdf"
        scores = output_dir / "docking_scores.csv"
        raw: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {}
        summary_path = output_dir / "vina_run.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text())
                if not isinstance(summary, dict):
                    raise ValueError("expected a JSON object")
                metadata = {
                    "failed_ligands": summary.get("failed_ligands", []),
                    "successful_ligands": summary.get("successful_ligands", 0),
                    "run_summary": str(summary_path),
                    "run_summary_data": summary,
                }
            except Exception as exc:
                output_errors.append(f"Could not read Vina run summary {summary_path}: {exc}")
        else:
            output_errors.append(f"Vina run summary is missing: {summary_path}")
        for path, name in ((poses, "docked_poses"), (scores, "docking_scores")):
            if not path.exists():
                output_errors.append(f"Vina output file is missing: {path}")
                continue
            try:
                if name == "docked_poses":
                    raw[name] = Poses.from_file(path, "sdf", metadata)
                else:
                    raw[name] = Scores.from_file(path, "csv", {**metadata, "columns": DOCKING_SCORE_COLUMNS})
            except Exception as exc:
                output_errors.append(f"Could not read Vina {name} output {path}: {exc}")
        return raw
