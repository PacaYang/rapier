"""Smina docking method.

Smina is a fork of AutoDock Vina with custom scoring functions and
enhanced features for virtual screening.
"""

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...artifacts import Artifact
from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import BindingSite, Molecules, Poses, ProteinStructure, Scores
from ..base_implementations import ParallelizableMethod
from ..utils import (
    DOCKING_SCORE_COLUMNS, parse_vina_output, resolve_box, run_command,
    validate_pdb_file, validate_sdf_file,
)


def _unique_ligand_ids(records: Sequence[Any]) -> List[str]:
    """Mirror the output naming rule while avoiding collisions with source IDs."""
    used: set[str] = set()
    reserved = {record.id for record in records}
    result: List[str] = []
    for record in records:
        base = record.id
        candidate = base
        suffix = 2
        while candidate in used or candidate != base and candidate in reserved:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _score_rows(scores: Artifact, errors: List[str]) -> List[Dict[str, Any]]:
    try:
        rows, row_errors = scores.rows_with_errors()
        errors.extend(f"Could not read docking score table: {error}" for error in row_errors)
        return rows
    except Exception as exc:
        errors.append(f"Could not read docking score table: {exc}")
        return []


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any, minimum: int = 0) -> Optional[int]:
    number = _numeric(value)
    return int(number) if number is not None and number.is_integer() and number >= minimum else None


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
        box = resolve_box(inputs.get("binding_site"), self.config["box_center"], self.config["box_size"])
        for key in ("center", "size"):
            values = box.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != 3 or any(
                _numeric(value) is None or key == "size" and _numeric(value) <= 0 for value in values
            ):
                raise ValueError(f"Docking box {key} needs three finite {'positive ' if key == 'size' else ''}numbers")
        return box

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        # Content and search-box prerequisites are checked together on the
        # execution host, after structural input validation.
        return []

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        """Check file readability and the nonempty ligand inventory before docking."""
        errors: List[str] = []
        receptor = inputs.get("receptor")
        ligands = inputs.get("ligands")
        if not isinstance(receptor, Artifact):
            errors.append("Smina requires a normalized receptor structure")
        elif not receptor.is_file or not receptor.path.is_file():
            errors.append("Smina receptor must be a readable file")
        else:
            try:
                valid, message = validate_pdb_file(receptor.path)
                if not valid:
                    errors.append(message)
                atom_count = 0
                for line_number, line in enumerate(receptor.path.read_text().splitlines(), 1):
                    if line.startswith(("ATOM  ", "HETATM")):
                        atom_count += 1
                        if any(_numeric(line[start:start + 8]) is None for start in (30, 38, 46)):
                            errors.append(f"Smina receptor has invalid atom coordinates on line {line_number}")
                if valid and not atom_count:
                    errors.append("Smina receptor has no readable ATOM/HETATM records")
            except (OSError, UnicodeError) as exc:
                errors.append(f"Could not read Smina receptor: {exc}")
        if not isinstance(ligands, Artifact):
            errors.append("Smina requires a normalized ligand collection")
        else:
            try:
                valid, message = validate_sdf_file(ligands.path)
                if not valid:
                    errors.append(message)
                records = ligands.records()
                if not records:
                    errors.append("No ligands in input file")
                for record in records:
                    if not record.heavy_atoms or any(
                        _numeric(value) is None for point in record.heavy_atoms for value in point
                    ):
                        errors.append(f"Smina input ligand {record.index} ({record.id}) has no usable heavy-atom coordinates")
            except Exception as exc:
                errors.append(f"Could not read Smina ligand inventory: {exc}")
        try:
            self._box(inputs)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
        return errors

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Validate the complete pose/score pool and per-input outcome accounting."""
        errors: List[str] = []
        expected_records = []
        try:
            expected_records = inputs["ligands"].records()
        except Exception as exc:
            errors.append(f"Could not read Smina input inventory during output validation: {exc}")
        expected_ids = _unique_ligand_ids(expected_records)
        expected_by_index = {
            int(record.index): ligand_id for record, ligand_id in zip(expected_records, expected_ids)
        }

        pose_artifact = outputs.get("docked_poses")
        score_artifact = outputs.get("docking_scores")
        if not isinstance(pose_artifact, Artifact):
            errors.append("Missing docked_poses output")
        if not isinstance(score_artifact, Artifact):
            errors.append("Missing docking_scores output")

        artifacts = [(name, value) for name, value in (("docked_poses", pose_artifact), ("docking_scores", score_artifact))
                     if isinstance(value, Artifact)]
        outcome_values = [(name, value.metadata.get("ligand_outcomes")) for name, value in artifacts]
        for name, value in outcome_values:
            if not isinstance(value, list):
                errors.append(f"Smina {name} is missing per-ligand outcome metadata")
        if len(outcome_values) == 2 and outcome_values[0][1] != outcome_values[1][1]:
            errors.append("Smina pose and score ligand outcome metadata disagree")
        outcomes = next((value for _, value in outcome_values if isinstance(value, list)), None)
        if not isinstance(outcomes, list):
            errors.append("Smina outputs are missing per-ligand outcome metadata")
            outcomes = []
        outcome_indexes: List[int] = []
        success_ids: List[str] = []
        failed_ids: List[str] = []
        for position, outcome in enumerate(outcomes):
            if not isinstance(outcome, dict):
                errors.append(f"Ligand outcome {position} is malformed")
                continue
            index = _integer(outcome.get("index"))
            if index is None:
                errors.append(f"Ligand outcome {position} has no valid input index")
            else:
                outcome_indexes.append(index)
            expected_id = expected_by_index.get(index) if index is not None else None
            ligand_id = str(outcome.get("id", ""))
            if not isinstance(outcome.get("id"), str) or not ligand_id.strip():
                errors.append(f"Ligand outcome {position} needs a nonempty string id")
            if expected_id is None and index is not None:
                errors.append(f"Ligand outcome references unexpected input index {index}")
            elif expected_id is not None and ligand_id != expected_id:
                errors.append(
                    f"Ligand outcome at input index {index} has ID '{ligand_id}', expected '{expected_id}'"
                )
            if outcome.get("success") is True:
                success_ids.append(ligand_id)
            elif outcome.get("success") is False:
                failed_ids.append(ligand_id)
                if not isinstance(outcome.get("error"), str) or not outcome["error"].strip():
                    errors.append(f"Failed ligand '{ligand_id}' has no failure reason")
            else:
                errors.append(f"Ligand outcome for '{ligand_id}' has no boolean success value")
        if len(outcome_indexes) != len(set(outcome_indexes)):
            errors.append("Smina ligand outcome metadata contains duplicate input indexes")
        missing_indexes = sorted(set(expected_by_index) - set(outcome_indexes))
        extra_indexes = sorted(set(outcome_indexes) - set(expected_by_index))
        if missing_indexes:
            errors.append(f"Smina did not account for input ligand indexes: {missing_indexes}")
        if extra_indexes:
            errors.append(f"Smina outcome metadata has unexpected input indexes: {extra_indexes}")
        if len(success_ids) != len(set(success_ids)):
            errors.append("Smina outcome metadata contains duplicate successful ligand IDs")
        if not success_ids:
            errors.append("Smina produced no successful ligand poses")
        for name, artifact in artifacts:
            metadata = artifact.metadata
            if "successful_ligands" in metadata and _integer(metadata["successful_ligands"]) != len(success_ids):
                errors.append(f"Smina {name} successful_ligands count does not match outcomes")
            if "failed_ligands" in metadata:
                failures = metadata["failed_ligands"]
                if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures) or Counter(failures) != Counter(failed_ids):
                    errors.append(f"Smina {name} failed_ligands inventory does not match outcomes")

        if isinstance(pose_artifact, Artifact) or isinstance(score_artifact, Artifact):
            pose_keys = []
            rows = _score_rows(score_artifact, errors) if isinstance(score_artifact, Artifact) else []
            if isinstance(pose_artifact, Artifact):
                try:
                    for record in pose_artifact.records():
                        pose_keys.append((record.id, record.pose))
                        if record.id not in success_ids:
                            errors.append(f"Smina pose ({record.id}, {record.pose}) references a non-successful ligand")
                        if not record.heavy_atoms:
                            errors.append(f"Smina pose ({record.id}, {record.pose}) has no readable heavy-atom coordinates")
                        elif any(not all(math.isfinite(value) for value in xyz) for xyz in record.heavy_atoms):
                            errors.append(f"Smina pose ({record.id}, {record.pose}) has non-finite coordinates")
                except Exception as exc:
                    errors.append(f"Could not read Smina pose coordinates: {exc}")
            score_keys: List[Tuple[str, int]] = []
            seen_score_keys: set[Tuple[str, int]] = set()
            for row_index, row in enumerate(rows):
                identifier = row.get("id", row.get("ligand", ""))
                ligand_id = str(identifier).strip()
                if not isinstance(identifier, str) or not ligand_id:
                    errors.append(f"Docking score row {row_index} needs a nonempty string id")
                pose = _integer(row.get("pose", row.get("mode")), 1)
                if pose is None:
                    errors.append(f"Docking score row {row_index} has no valid pose number")
                else:
                    key = (ligand_id, pose)
                    score_keys.append(key)
                    if key in seen_score_keys:
                        errors.append(f"Duplicate docking score key {key}")
                    seen_score_keys.add(key)
                if ligand_id not in success_ids:
                    errors.append(f"Docking score row {row_index} references non-successful ligand '{ligand_id}'")
                if _numeric(row.get("affinity_kcal_mol", row.get("affinity"))) is None:
                    errors.append(f"Docking score row {row_index} has invalid affinity")
                for column in ("rmsd_lb", "rmsd_ub"):
                    if column not in row or _numeric(row.get(column)) is None:
                        errors.append(f"Docking score row {row_index} has invalid {column}")
            # Poses.records numbers each same-ID record in file order. Compare
            # those occurrence keys and counts directly with the score keys.
            for key, count in Counter(pose_keys).items():
                if count > 1:
                    errors.append(f"Duplicate docked pose key {key}")
            if isinstance(pose_artifact, Artifact) and isinstance(score_artifact, Artifact) and Counter(pose_keys) != Counter(score_keys):
                errors.append(
                    "Docked pose and score inventories differ "
                    f"(poses-only={sorted(set(pose_keys) - set(score_keys))}, "
                    f"scores-only={sorted(set(score_keys) - set(pose_keys))})"
                )
            represented = {ligand_id for ligand_id, _ in score_keys}
            unrepresented_successes = sorted(set(success_ids) - represented)
            if unrepresented_successes:
                errors.append(f"Successful ligands have no score/pose rows: {unrepresented_successes}")
        return errors

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        box = self._box(inputs)
        temp_dir = ctx.work_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        records = inputs["ligands"].records()
        if not records:
            raise MethodError("No ligands in input file")
        rows: List[Dict[str, Any]] = []
        blocks: List[str] = []
        outcomes: List[Dict[str, Any]] = []
        output_names = _unique_ligand_ids(records)
        for record, ligand_id in zip(records, output_names):
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
            returncode, stdout, stderr = run_command(command, cwd=ctx.work_dir)
            if returncode != 0 or not out_sdf.exists():
                outcomes.append({
                    "index": record.index, "id": ligand_id, "success": False,
                    "error": stderr or stdout or "Smina failed or produced no output pose file",
                })
                continue
            # Title every pose with the molecule id so poses and scores join.
            try:
                ligand_poses = Poses.from_file(out_sdf).records()
                ligand_scores = parse_vina_output(stdout)
            except Exception as exc:
                outcomes.append({
                    "index": record.index, "id": ligand_id, "success": False,
                    "error": f"Could not collect Smina ligand results: {exc}",
                })
                continue
            if not ligand_poses or not ligand_scores:
                outcomes.append({
                    "index": record.index, "id": ligand_id, "success": False,
                    "error": "Smina output did not contain both a pose and parseable score",
                })
                continue
            for pose in ligand_poses:
                lines = pose.block.splitlines()
                blocks.append("\n".join([ligand_id] + lines[1:]) + "\n")
            for score in ligand_scores:
                rows.append({"id": ligand_id, "pose": score["mode"], "affinity_kcal_mol": score["affinity"],
                             "rmsd_lb": score["rmsd_lb"], "rmsd_ub": score["rmsd_ub"],
                             "scoring_function": self.config["scoring"]})
            outcomes.append({"index": record.index, "id": ligand_id, "success": True})
        metadata = {
            "columns": DOCKING_SCORE_COLUMNS,
            "ligand_outcomes": outcomes,
            "successful_ligands": sum(item["success"] for item in outcomes),
            "failed_ligands": [item["id"] for item in outcomes if not item["success"]],
        }
        return {
            "docked_poses": Poses.write_sdf(blocks, ctx.output_dir / "docked_poses.sdf", metadata),
            "docking_scores": Scores.from_records(
                rows, ctx.output_dir / "docking_scores.csv", metadata=metadata,
            ),
        }
