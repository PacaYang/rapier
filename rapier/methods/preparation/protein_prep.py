"""Protein preparation method for molecular docking.

Prepares receptor structures by removing waters/heteroatoms, adding hydrogens
and (optionally) converting to PDBQT with Open Babel.
"""

import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import ProteinStructure, Report
from ..utils import run_command, validate_pdb_file


class ProteinPreparation(Method):
    """Prepare protein receptor structures for docking.

    This method prepares proteins by:
    1. Removing water molecules and (optionally) heteroatoms
    2. Fixing structure and adding hydrogens with Open Babel
    3. Converting to PDBQT with charges (if requested)

    Parameters:
        - ph: pH for protonation (default: 7.4)
        - remove_waters: Remove water molecules (default: True)
        - remove_hetero: Remove heteroatoms except water (default: False)
        - add_charges: Add Gasteiger charges (default: True)
        - output_format: Output format ('pdb', 'pdbqt', default: 'pdbqt')
    """

    register_name = "protein_prep"
    display_name = "Protein Preparation"
    category = "preparation"
    inputs = {
        "receptor": PortSpec(
            ProteinStructure, "pdb",
            description="Protein structure; CIF is converted when an adapter is available",
        ),
    }
    outputs = {
        "prepared_receptor": PortSpec(ProteinStructure, ("pdb", "pdbqt"), description="Prepared receptor"),
        "preparation_log": PortSpec(Report, "json", description="Steps applied"),
    }
    config_fields = {
        "ph": ConfigField((int, float), default=7.4, minimum=0, maximum=14),
        "remove_waters": ConfigField(bool, default=True),
        "remove_hetero": ConfigField(bool, default=False),
        "add_charges": ConfigField(bool, default=True),
        "output_format": ConfigField(str, default="pdbqt", choices=("pdb", "pdbqt")),
    }
    resources = ResourceSpec(cpus=1, memory="4GB", time="00:30:00", partition="cpu")
    environment = EnvironmentSpec(executables=["obabel"])

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        # Keep the input contract cheap; execution-host content checks belong
        # in check_dependencies so local and scheduled runs share them.
        return []

    @staticmethod
    def _structure_errors(artifact: Any, label: str) -> List[str]:
        errors: List[str] = []
        try:
            valid, error = validate_pdb_file(artifact.path)
            if not valid:
                return [f"{label}: {error}"]
            atom_count = 0
            for number, line in enumerate(artifact.path.read_text().splitlines(), 1):
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                atom_count += 1
                try:
                    coordinates = [float(line[start:start + 8]) for start in (30, 38, 46)]
                    if not all(math.isfinite(value) for value in coordinates):
                        raise ValueError("non-finite coordinates")
                except ValueError:
                    errors.append(f"{label}: invalid atom coordinates on line {number}")
            if not atom_count:
                errors.append(f"{label}: no valid ATOM/HETATM record prefixes")
        except (AttributeError, OSError, UnicodeError) as exc:
            errors.append(f"{label}: cannot read structure: {exc}")
        return errors

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        receptor = inputs.get("receptor")
        if receptor is None:
            return ["receptor is required"]
        errors = self._structure_errors(receptor, "receptor")
        if receptor.format != "pdb":
            errors.append("receptor must be converted to PDB before preparation")
        return errors

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        errors: List[str] = []
        receptor = outputs.get("prepared_receptor")
        if receptor is None:
            errors.append("prepared_receptor is missing")
        else:
            errors.extend(self._structure_errors(receptor, "prepared_receptor"))
            expected = self.config["output_format"]
            if receptor.format != expected:
                errors.append(f"prepared_receptor format must be {expected}")
        try:
            log = outputs["preparation_log"].load()
            if not isinstance(log, dict):
                errors.append("preparation_log must be a JSON object")
            else:
                for key in ("steps", "warnings"):
                    values = log.get(key)
                    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                        errors.append(f"preparation_log.{key} must be a list of strings")
                if isinstance(log.get("steps"), list) and not log["steps"]:
                    errors.append("preparation_log.steps must describe the preparation")
        except (KeyError, AttributeError, OSError, ValueError, UnicodeError) as exc:
            errors.append(f"preparation_log cannot be read: {exc}")
        return errors

    def _remove_waters_and_hetero(self, pdb_path: Path, output_path: Path) -> None:
        with open(pdb_path) as f_in, open(output_path, "w") as f_out:
            for line in f_in:
                if self.config["remove_waters"] and "HOH" in line:
                    continue
                if self.config["remove_hetero"] and line.startswith("HETATM"):
                    continue
                f_out.write(line)

    def _obabel(self, source: Path, target: Path, *flags: str) -> Tuple[bool, str]:
        returncode, _, stderr = run_command(["obabel", str(source), "-O", str(target), *flags])
        return returncode == 0, stderr

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        temp_dir = ctx.work_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        log: Dict[str, Any] = {"steps": [], "warnings": []}
        current = inputs["receptor"].path

        if self.config["remove_waters"] or self.config["remove_hetero"]:
            cleaned = temp_dir / "cleaned.pdb"
            self._remove_waters_and_hetero(current, cleaned)
            current = cleaned
            log["steps"].append("Removed waters/heteroatoms")

        fixed = temp_dir / "fixed.pdb"
        ok, stderr = self._obabel(current, fixed, "-h")
        if not ok:
            raise MethodError(f"Structure fixing failed: {stderr}")
        log["steps"].append("Fixed structure and added hydrogens")

        if self.config["output_format"] == "pdbqt":
            final = ctx.output_dir / "prepared_receptor.pdbqt"
            flags = ["-xr"] + (["-p", str(self.config["ph"])] if self.config["add_charges"] else [])
            ok, stderr = self._obabel(fixed, final, *flags)
            if not ok:
                raise MethodError(f"PDBQT conversion failed: {stderr}")
            log["steps"].append("Converted to PDBQT with charges")
        else:
            final = ctx.output_dir / "prepared_receptor.pdb"
            shutil.copy(fixed, final)
            log["steps"].append("Output as PDB")

        return {"prepared_receptor": final, "preparation_log": log}
