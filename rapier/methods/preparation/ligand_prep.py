"""Ligand preparation method for molecular docking.

Generates 3D conformations with hydrogens for SMILES or SDF inputs and
optionally energy-minimizes them. Molecule names are kept so prepared ligands
still join with upstream scores by id.
"""

import math
from collections import Counter
from typing import Any, Dict, List

from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import Molecules, Report
from ..utils import rdkit_molecules, write_rdkit_sdf


class LigandPreparation(Method):
    """Prepare ligand molecules for docking.

    Steps: build molecules from SMILES or SDF, add hydrogens, embed a 3D
    conformer (ETKDGv3, seed 42) and optionally minimize with UFF or MMFF.

    Parameters:
        - ph: pH for protonation (default: 7.4)
        - num_conformers: Number of conformers to generate (default: 1)
        - energy_minimize: Whether to minimize energy (default: True)
        - force_field: Force field for minimization ('UFF', 'MMFF', default: 'UFF')
    """

    register_name = "ligand_prep"
    display_name = "Ligand Preparation"
    category = "preparation"
    inputs = {
        "ligands": PortSpec(Molecules, ("smiles", "sdf"), description="Input molecules"),
    }
    outputs = {
        "prepared_ligands": PortSpec(Molecules, "sdf", description="3D, protonated molecules"),
        "preparation_log": PortSpec(Report, "json", description="Per-molecule successes and failures"),
    }
    config_fields = {
        "ph": ConfigField((int, float), default=7.4, minimum=0, maximum=14),
        "num_conformers": ConfigField(int, default=1, minimum=1),
        "energy_minimize": ConfigField(bool, default=True),
        "force_field": ConfigField(str, default="UFF", choices=("UFF", "MMFF")),
    }
    resources = ResourceSpec(cpus=2, memory="4GB", time="02:00:00", partition="cpu")
    environment = EnvironmentSpec(python_packages=["rdkit"])

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        try:
            records = inputs["ligands"].records()
        except (KeyError, AttributeError, OSError, ValueError, UnicodeError) as exc:
            return [f"ligands cannot be read: {exc}"]
        return [] if records else ["No molecules in input"]

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Validate the full result inventory, including failed input occurrences."""
        errors: List[str] = []
        expected = None
        try:
            expected = Counter((record.id, record.pose) for record in inputs["ligands"].records())
        except (KeyError, AttributeError, OSError, ValueError, UnicodeError) as exc:
            errors.append(f"input ligand inventory cannot be read: {exc}")

        prepared_keys = []
        prepared_count = None
        try:
            prepared = rdkit_molecules(outputs["prepared_ligands"])
            prepared_count = len(prepared)
            for record, mol in prepared:
                try:
                    occurrence = int(record.properties["rapier_input_occurrence"])
                    if occurrence < 1:
                        raise ValueError("occurrence must be positive")
                    prepared_keys.append((record.id, occurrence))
                except (KeyError, TypeError, ValueError):
                    errors.append(f"prepared ligand {record.index + 1} lacks a valid input occurrence")
                if mol is None:
                    errors.append(f"prepared ligand {record.index + 1} ({record.id}) is unparsable")
                    continue
                if not mol.GetNumHeavyAtoms():
                    errors.append(f"prepared ligand {record.index + 1} ({record.id}) has no heavy atoms")
                if not mol.GetNumConformers() or not mol.GetConformer().Is3D():
                    errors.append(f"prepared ligand {record.index + 1} ({record.id}) has no 3D conformer")
                elif not all(math.isfinite(value) for point in mol.GetConformer().GetPositions() for value in point):
                    errors.append(f"prepared ligand {record.index + 1} ({record.id}) has non-finite coordinates")
            if not prepared:
                errors.append("No molecules were successfully prepared")
            if len(prepared_keys) != len(set(prepared_keys)):
                errors.append("prepared_ligands contains duplicate input occurrences")
        except (KeyError, AttributeError, ImportError, OSError, ValueError, UnicodeError) as exc:
            errors.append(f"prepared_ligands cannot be read: {exc}")

        log_loaded = False
        try:
            log = outputs["preparation_log"].load()
            log_loaded = True
        except (KeyError, AttributeError, OSError, ValueError, UnicodeError) as exc:
            errors.append(f"preparation_log cannot be read: {exc}")
            log = None
        if log_loaded and not isinstance(log, dict):
            errors.append("preparation_log must be a JSON object")
        if isinstance(log, dict):
            counts = {}
            for key in ("total", "successful", "failed"):
                value = log.get(key)
                if type(value) is not int or value < 0:
                    errors.append(f"preparation_log.{key} must be a nonnegative integer")
                else:
                    counts[key] = value
            if len(counts) == 3 and counts["total"] != counts["successful"] + counts["failed"]:
                errors.append("preparation_log total must equal successful + failed")
            if expected is not None and counts.get("total") != sum(expected.values()):
                errors.append("preparation_log total does not match the input inventory")
            outcomes = {}
            for key, count_key in (("successful_molecules", "successful"), ("failed_molecules", "failed")):
                entries = log.get(key)
                if not isinstance(entries, list):
                    errors.append(f"preparation_log.{key} must be a list")
                    continue
                keys = []
                for index, entry in enumerate(entries, 1):
                    if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                            or type(entry.get("occurrence")) is not int or entry["occurrence"] < 1):
                        errors.append(f"preparation_log.{key}[{index}] must identify an input occurrence")
                        continue
                    keys.append((entry["id"], entry["occurrence"]))
                    if key == "failed_molecules" and not (isinstance(entry.get("error"), str) and entry["error"].strip()):
                        errors.append(f"preparation_log.failed_molecules[{index}] must explain the failure")
                outcomes[key] = Counter(keys)
                if counts.get(count_key) != len(entries):
                    errors.append(f"preparation_log.{count_key} does not match {key}")
                if len(keys) != len(set(keys)):
                    errors.append(f"preparation_log.{key} contains duplicate input occurrences")
            successes = outcomes.get("successful_molecules")
            failures = outcomes.get("failed_molecules")
            if successes is not None and prepared_count is not None:
                if successes != Counter(prepared_keys) or counts.get("successful") != prepared_count:
                    errors.append("prepared_ligands does not match successful_molecules")
            if expected is not None and successes is not None and failures is not None:
                if successes + failures != expected:
                    errors.append("successful and failed molecules must partition every input occurrence")
        return errors

    def _generate_3d_conformer(self, mol):
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = 42  # For reproducibility
        if AllChem.EmbedMolecule(mol, params) != 0:
            # Fall back to random coordinates if ETKDG fails.
            if AllChem.EmbedMolecule(mol, useRandomCoords=True, randomSeed=42) != 0:
                raise RuntimeError("RDKit failed to generate a 3D conformer")
        return mol

    def _minimize_energy(self, mol):
        from rdkit.Chem import AllChem

        if self.config["force_field"] == "MMFF":
            try:
                AllChem.MMFFOptimizeMolecule(mol)
            except Exception:
                AllChem.UFFOptimizeMolecule(mol)  # Fall back to UFF if MMFF fails.
        else:
            AllChem.UFFOptimizeMolecule(mol)
        return mol

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        molecules = rdkit_molecules(inputs["ligands"])
        if not any(mol is not None for _, mol in molecules):
            raise MethodError("No valid molecules in input")
        prepared = []
        log: Dict[str, Any] = {
            "total": len(molecules), "successful": 0, "failed": 0,
            "successful_molecules": [], "failed_molecules": [],
        }
        for record, mol in molecules:
            identity = {"name": record.name, "id": record.id, "occurrence": record.pose, "index": record.index}
            try:
                if mol is None:
                    raise ValueError("RDKit could not parse the input molecule")
                mol_3d = self._generate_3d_conformer(mol)
                if self.config["energy_minimize"]:
                    mol_3d = self._minimize_energy(mol_3d)
                mol_3d.SetProp("rapier_input_occurrence", str(record.pose))
                prepared.append(mol_3d)
                log["successful"] += 1
                log["successful_molecules"].append(identity)
            except Exception as exc:
                log["failed"] += 1
                log["failed_molecules"].append({**identity, "error": str(exc)})
        return {
            "prepared_ligands": write_rdkit_sdf(prepared, ctx.output_dir / "prepared_ligands.sdf"),
            "preparation_log": log,
        }
