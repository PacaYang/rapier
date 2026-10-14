"""Smina docking method.

Smina is a fork of AutoDock Vina with custom scoring functions and
enhanced features for virtual screening.
"""

from typing import Any, Dict, List

from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import BindingSite, Molecules, Poses, ProteinStructure, Scores
from ..base_implementations import ParallelizableMethod
from ..utils import DOCKING_SCORE_COLUMNS, parse_vina_output, resolve_box, run_command


class SminaDocking(Method, ParallelizableMethod):
    """Molecular docking using Smina.

    Smina reads PDB receptors and SDF ligands directly. Outputs match
    ``vina_docking``: poses titled by molecule id and a score table keyed by
    ``id``/``pose`` with ``affinity_kcal_mol``.

    Parameters:
        - box_center / box_size: Deprecated fallback when no binding_site input is bound
        - exhaustiveness: Search exhaustiveness (default: 8)
        - num_modes: Number of binding modes (default: 9)
        - scoring: Scoring function ('vina', 'vinardo', 'ad4', default: 'vina')
        - minimize: Energy minimize poses (default: True)
    """

    register_name = "smina_docking"
    display_name = "Smina Docking"
    category = "docking"
    inputs = {
        "receptor": PortSpec(ProteinStructure, ("pdb", "pdbqt")),
        "ligands": PortSpec(Molecules, "sdf"),
        "binding_site": PortSpec(BindingSite, "box", required=False, description="Docking search box"),
    }
    outputs = {
        "docked_poses": PortSpec(Poses, "sdf", description="Docked poses"),
        "docking_scores": PortSpec(Scores, "csv", description="id, pose, affinity_kcal_mol, rmsd_lb, rmsd_ub"),
    }
    config_fields = {
        "box_center": ConfigField(list, default=[0.0, 0.0, 0.0]),
        "box_size": ConfigField(list, default=[20.0, 20.0, 20.0]),
        "exhaustiveness": ConfigField(int, default=8, minimum=1),
        "num_modes": ConfigField(int, default=9, minimum=1),
        "scoring": ConfigField(str, default="vina", choices=("vina", "vinardo", "ad4")),
        "minimize": ConfigField(bool, default=True),
    }
    resources = ResourceSpec(cpus=4, memory="8GB", time="02:00:00", partition="cpu")
    environment = EnvironmentSpec(executables=["smina"])

    def _box(self, inputs: Dict[str, Any]) -> Dict[str, List[float]]:
        return resolve_box(inputs.get("binding_site"), self.config["box_center"], self.config["box_size"])

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        try:
            self._box(inputs)
        except (TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        box = self._box(inputs)
        temp_dir = ctx.work_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        records = inputs["ligands"].records()
        if not records:
            raise MethodError("No ligands in input file")
        rows: List[Dict[str, Any]] = []
        blocks: List[str] = []
        used: Dict[str, int] = {}
        for record in records:
            # Repeated titles get -2, -3, ... like the Vina runner, so ids stay unique.
            used[record.id] = used.get(record.id, 0) + 1
            ligand_id = record.id if used[record.id] == 1 else f"{record.id}-{used[record.id]}"
            ligand = temp_dir / f"{record.index}_{record.id}.sdf"
            Molecules.write_sdf([record.block], ligand)
            out_sdf = temp_dir / f"{record.index}_{record.id}_out.sdf"
            command = [
                "smina", "--receptor", str(inputs["receptor"].path), "--ligand", str(ligand),
                "--out", str(out_sdf),
                "--center_x", str(box["center"][0]), "--center_y", str(box["center"][1]),
                "--center_z", str(box["center"][2]), "--size_x", str(box["size"][0]),
                "--size_y", str(box["size"][1]), "--size_z", str(box["size"][2]),
                "--exhaustiveness", str(self.config["exhaustiveness"]),
                "--num_modes", str(self.config["num_modes"]), "--scoring", self.config["scoring"],
            ]
            if self.config["minimize"]:
                command.append("--minimize")
            returncode, stdout, _ = run_command(command, cwd=ctx.work_dir)
            if returncode != 0 or not out_sdf.exists():
                continue
            # Title every pose with the molecule id so poses and scores join.
            for pose in Poses.from_file(out_sdf).records():
                lines = pose.block.splitlines()
                blocks.append("\n".join([ligand_id] + lines[1:]) + "\n")
            for score in parse_vina_output(stdout):
                rows.append({"id": ligand_id, "pose": score["mode"], "affinity_kcal_mol": score["affinity"],
                             "rmsd_lb": score["rmsd_lb"], "rmsd_ub": score["rmsd_ub"],
                             "scoring_function": self.config["scoring"]})
        return {
            "docked_poses": Poses.write_sdf(blocks, ctx.output_dir / "docked_poses.sdf"),
            "docking_scores": Scores.from_records(
                rows, ctx.output_dir / "docking_scores.csv", metadata={"columns": DOCKING_SCORE_COLUMNS},
            ),
        }
