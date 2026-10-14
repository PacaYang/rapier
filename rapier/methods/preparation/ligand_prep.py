"""Ligand preparation method for molecular docking.

Generates 3D conformations with hydrogens for SMILES or SDF inputs and
optionally energy-minimizes them. Molecule names are kept so prepared ligands
still join with upstream scores by id.
"""

from typing import Any, Dict

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
        molecules = [(record, mol) for record, mol in rdkit_molecules(inputs["ligands"]) if mol is not None]
        if not molecules:
            raise MethodError("No valid molecules in input")
        prepared = []
        log: Dict[str, Any] = {"total": len(molecules), "successful": 0, "failed": 0, "failed_molecules": []}
        for record, mol in molecules:
            try:
                mol_3d = self._generate_3d_conformer(mol)
                if self.config["energy_minimize"]:
                    mol_3d = self._minimize_energy(mol_3d)
                prepared.append(mol_3d)
                log["successful"] += 1
            except Exception as exc:
                log["failed"] += 1
                log["failed_molecules"].append({"name": record.name, "id": record.id, "error": str(exc)})
        return {
            "prepared_ligands": write_rdkit_sdf(prepared, ctx.output_dir / "prepared_ligands.sdf"),
            "preparation_log": log,
        }
