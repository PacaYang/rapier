"""Ligand filtering method for property-based molecular screening.

Filters molecules by drug-likeness criteria (Lipinski's Rule of Five and
related physicochemical properties) and reports every molecule's computed
properties as an id-keyed score table, so a downstream ``molecule_filter``
can rank or gate on them.
"""

from typing import Any, Dict, List

from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import Molecules, Report, Scores
from ..utils import rdkit_molecules, write_rdkit_sdf

PROPERTY_UNITS = {"mw": "Da", "tpsa": "A^2"}


class LigandFilter(Method):
    """Filter molecules by physicochemical properties.

    Molecules that pass every bound are written to ``filtered_ligands``
    (verbatim for SDF input). ``properties`` lists mw, logp, hba, hbd, tpsa,
    rotatable_bonds and pass/fail for every input molecule.

    Filters available:
        - mw_min, mw_max: Molecular weight bounds (Da)
        - logp_min, logp_max: LogP (lipophilicity) bounds
        - hba_max: Hydrogen bond acceptors maximum
        - hbd_max: Hydrogen bond donors maximum
        - tpsa_max: Topological polar surface area maximum (Ų)
        - rotatable_bonds_max: Maximum rotatable bonds

    Default Lipinski's Rule of Five:
        - MW ≤ 500 Da
        - LogP ≤ 5
        - HBA ≤ 10
        - HBD ≤ 5
    """

    register_name = "ligand_filter"
    display_name = "Ligand Filter"
    category = "filter"
    inputs = {
        "ligands": PortSpec(Molecules, ("sdf", "smiles"), description="Molecules to filter"),
    }
    outputs = {
        "filtered_ligands": PortSpec(Molecules, "sdf", description="Molecules passing the filters"),
        "properties": PortSpec(Scores, "csv", description="Computed properties of every molecule"),
        "statistics": PortSpec(Report, "json", description="Filtering statistics"),
    }
    config_fields = {
        "mw_min": ConfigField((int, float), default=0),
        "mw_max": ConfigField((int, float), default=500),
        "logp_min": ConfigField((int, float), default=-5),
        "logp_max": ConfigField((int, float), default=5),
        "hba_max": ConfigField(int, default=10, minimum=0),
        "hbd_max": ConfigField(int, default=5, minimum=0),
        "tpsa_max": ConfigField((int, float), default=140, minimum=0),
        "rotatable_bonds_max": ConfigField(int, default=10, minimum=0),
    }
    resources = ResourceSpec(cpus=1, memory="4GB", time="01:00:00", partition="cpu")
    environment = EnvironmentSpec(python_packages=["rdkit"])

    def check_config(self, config: Dict[str, Any]) -> List[str]:
        errors = []
        if config.get("mw_min", 0) > config.get("mw_max", 500):
            errors.append("mw_min cannot be greater than mw_max")
        if config.get("logp_min", -5) > config.get("logp_max", 5):
            errors.append("logp_min cannot be greater than logp_max")
        return errors

    def _reasons(self, props: Dict[str, float]) -> List[str]:
        c = self.config
        checks = (
            ("molecular_weight", not c["mw_min"] <= props["mw"] <= c["mw_max"]),
            ("logp", not c["logp_min"] <= props["logp"] <= c["logp_max"]),
            ("hba", props["hba"] > c["hba_max"]),
            ("hbd", props["hbd"] > c["hbd_max"]),
            ("tpsa", props["tpsa"] > c["tpsa_max"]),
            ("rotatable_bonds", props["rotatable_bonds"] > c["rotatable_bonds_max"]),
        )
        return [reason for reason, failed in checks if failed]

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        from rdkit.Chem import Descriptors

        molecules = rdkit_molecules(inputs["ligands"])
        stats: Dict[str, Any] = {"total": len(molecules), "passed": 0, "failed": 0, "failed_by_reason": {}}
        rows, kept_blocks, kept_mols = [], [], []
        for record, mol in molecules:
            if mol is None:
                stats["failed"] += 1
                rows.append({"id": record.id, "passed": False, "reasons": "unparsable"})
                continue
            props = {
                "mw": round(Descriptors.MolWt(mol), 3),
                "logp": round(Descriptors.MolLogP(mol), 3),
                "hba": Descriptors.NOCount(mol),
                "hbd": Descriptors.NHOHCount(mol),
                "tpsa": round(Descriptors.TPSA(mol), 3),
                "rotatable_bonds": Descriptors.NumRotatableBonds(mol),
            }
            reasons = self._reasons(props)
            rows.append({"id": record.id, **props, "passed": not reasons, "reasons": ";".join(reasons)})
            if reasons:
                stats["failed"] += 1
                for reason in reasons:
                    stats["failed_by_reason"][reason] = stats["failed_by_reason"].get(reason, 0) + 1
            else:
                stats["passed"] += 1
                kept_blocks.append(record.block)
                kept_mols.append(mol)

        path = ctx.output_dir / "filtered.sdf"
        if inputs["ligands"].format == "sdf":
            filtered = Molecules.write_sdf(kept_blocks, path)  # Keep coordinates and hydrogens verbatim.
        else:
            filtered = write_rdkit_sdf(kept_mols, path)
        return {
            "filtered_ligands": filtered,
            "properties": Scores.from_records(rows, ctx.output_dir / "properties.csv", units=PROPERTY_UNITS),
            "statistics": stats,
        }
