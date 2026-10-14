"""AutoDock Vina docking method."""

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...core.task import Task
from ...data import BindingSite, Molecules, Poses, ProteinStructure, Scores
from ..base_implementations import ParallelizableMethod
from ..utils import DOCKING_SCORE_COLUMNS, check_executable, resolve_box, validate_pdb_file, validate_sdf_file
from .vina_runner import run_manifest


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
        errors = []
        receptor = inputs["receptor"]
        if receptor.format in ("pdb", "pdbqt"):
            valid, error = validate_pdb_file(receptor.path)
            if not valid:
                errors.append(error)
        elif receptor.path.stat().st_size == 0:
            errors.append(f"Structure file is empty: {receptor.path}")
        valid, error = validate_sdf_file(inputs["ligands"].path)
        if not valid:
            errors.append(error)
        try:
            self._box(inputs)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
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
        """Command submitted to SLURM: the Vina runner on this task's manifest."""
        ctx = RunContext.for_task(task)
        task.inputs = self.format_inputs(task.inputs, ctx.input_dir)
        manifest = self._write_manifest(task.inputs, ctx.work_dir, ctx.resources)
        task.intermediate_outputs["vina_manifest"] = str(manifest)
        return shlex.join([sys.executable, "-m", "rapier.methods.docking.vina_runner",
                           "--manifest", str(manifest.resolve())])

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        manifest = self._write_manifest(inputs, ctx.work_dir, ctx.resources)
        ctx.task.intermediate_outputs["vina_manifest"] = str(manifest)
        run_manifest(manifest)
        return self._collect(ctx.output_dir)

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        ctx = RunContext.for_task(task)
        return self.format_outputs(self._collect(ctx.output_dir), ctx)

    @staticmethod
    def _collect(output_dir: Path) -> Dict[str, Any]:
        poses = output_dir / "docked_poses.sdf"
        scores = output_dir / "docking_scores.csv"
        if not poses.exists() or not scores.exists():
            raise FileNotFoundError("Vina outputs are incomplete; expected docked_poses.sdf and docking_scores.csv")
        metadata: Dict[str, Any] = {}
        summary_path = output_dir / "vina_run.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            metadata = {
                "failed_ligands": summary.get("failed_ligands", []),
                "successful_ligands": summary.get("successful_ligands", 0),
                "run_summary": str(summary_path),
            }
        return {
            "docked_poses": Poses.from_file(poses, "sdf", metadata),
            "docking_scores": Scores.from_file(scores, "csv", {**metadata, "columns": DOCKING_SCORE_COLUMNS}),
        }
