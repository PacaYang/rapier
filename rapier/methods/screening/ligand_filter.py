"""Ligand filtering method for property-based molecular screening.

Filters molecules by drug-likeness criteria (Lipinski's Rule of Five and
related physicochemical properties) and reports every molecule's computed
properties as an id-keyed score table, so a downstream ``molecule_filter``
can rank or gate on them.
"""

import re
from typing import Any, Dict, List

from ...core.method import Method, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import Molecules, Report, Scores
from ..utils import rdkit_molecules, write_rdkit_sdf
from ._validation import boolean, compare_inventory, integer, number, read_output, read_records

PROPERTY_UNITS = {"mw": "Da", "tpsa": "A^2"}


def _with_occurrence(block: str, occurrence: int) -> str:
    """Label the source occurrence while retaining the original SDF structure."""
    lines = block.splitlines()
    retained, index = [], 0
    while index < len(lines):
        if re.match(r"^>.*<input_occurrence>", lines[index]):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
        else:
            retained.append(lines[index])
        index += 1
    return "\n".join(retained).rstrip() + f"\n\n> <input_occurrence>\n{occurrence}\n\n"


class LigandFilter(Method):
    """Filter molecules by physicochemical properties.

    Molecules that pass every bound are written to ``filtered_ligands``
    (preserving SDF structures and adding ``input_occurrence``). ``properties`` lists mw, logp, hba, hbd, tpsa,
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

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors: List[str] = []
        # Invalid individual molecules are legitimate failed filter records.
        read_records(inputs, "ligands", errors)
        return errors

    def validate_outputs(self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors: List[str] = []
        source = read_records(inputs, "ligands", errors)
        filtered = read_output(outputs, "filtered_ligands", "records", errors)
        rows = read_output(outputs, "properties", "rows", errors)
        stats = read_output(outputs, "statistics", "load", errors)
        passed_keys, keys, reasons_count = [], [], {}
        passed_count = failed_count = 0
        for index, row in enumerate(rows or []):
            where = f"properties row {index + 1}"
            identifier = row.get("id")
            occurrence = integer(row.get("input_occurrence"), 1)
            if not isinstance(identifier, str) or not identifier or occurrence is None:
                errors.append(f"{where}: needs id and positive input_occurrence")
            else:
                keys.append((identifier, occurrence))
            passed = boolean(row.get("passed"))
            reasons = str(row.get("reasons") or "").split(";")
            reasons = [reason for reason in reasons if reason]
            if passed is None:
                errors.append(f"{where}: passed must be a boolean")
            elif passed:
                passed_count += 1
                if isinstance(identifier, str) and occurrence is not None:
                    passed_keys.append((identifier, occurrence))
            else:
                failed_count += 1
            if passed is not None and passed != (not reasons):
                errors.append(f"{where}: reasons disagree with passed")
            for reason in reasons:
                reasons_count[reason] = reasons_count.get(reason, 0) + 1
            if reasons != ["unparsable"]:
                props = {name: number(row.get(name)) for name in ("mw", "logp", "hba", "hbd", "tpsa", "rotatable_bonds")}
                for name, value in props.items():
                    if value is None or (name != "logp" and value < 0):
                        errors.append(f"{where}: {name} must be a finite {'nonnegative ' if name != 'logp' else ''}number")
                    elif name in ("hba", "hbd", "rotatable_bonds") and not value.is_integer():
                        errors.append(f"{where}: {name} must be an integer")
                if all(value is not None for value in props.values()) and reasons != self._reasons(props):
                    errors.append(f"{where}: rejection reasons disagree with property bounds")
        if source is not None and rows is not None:
            compare_inventory(keys, [(record.id, record.pose) for record in source], "properties input inventory", errors)
        filtered_keys = []
        for record in filtered or []:
            occurrence = integer(record.properties.get("input_occurrence"), 1)
            if occurrence is None:
                errors.append(f"filtered_ligands record {record.index + 1}: needs a positive input_occurrence property")
            else:
                filtered_keys.append((record.id, occurrence))
            if not record.heavy_atoms or any(number(value) is None for point in record.heavy_atoms for value in point):
                errors.append(f"filtered_ligands record {record.index + 1}: no usable heavy-atom coordinates")
        if rows is not None and filtered is not None:
            compare_inventory(filtered_keys, passed_keys, "filtered_ligands inventory", errors)
        if stats is not None:
            if not isinstance(stats, dict):
                errors.append("statistics must be a mapping")
            else:
                expected = {"total": len(source) if source is not None else len(rows or []),
                            "passed": passed_count, "failed": failed_count}
                for key, value in expected.items():
                    if integer(stats.get(key)) != value:
                        errors.append(f"statistics.{key} must equal {value}")
                if stats.get("failed_by_reason") != reasons_count:
                    errors.append("statistics.failed_by_reason disagrees with properties rows")
        return errors

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        from rdkit.Chem import Descriptors

        molecules = rdkit_molecules(inputs["ligands"])
        stats: Dict[str, Any] = {"total": len(molecules), "passed": 0, "failed": 0, "failed_by_reason": {}}
        rows, kept_blocks, kept_mols = [], [], []
        for record, mol in molecules:
            if mol is None:
                stats["failed"] += 1
                stats["failed_by_reason"]["unparsable"] = stats["failed_by_reason"].get("unparsable", 0) + 1
                rows.append({"id": record.id, "input_occurrence": record.pose, "passed": False, "reasons": "unparsable"})
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
            rows.append({"id": record.id, "input_occurrence": record.pose, **props, "passed": not reasons, "reasons": ";".join(reasons)})
            if reasons:
                stats["failed"] += 1
                for reason in reasons:
                    stats["failed_by_reason"][reason] = stats["failed_by_reason"].get(reason, 0) + 1
            else:
                stats["passed"] += 1
                if record.block is not None:
                    kept_blocks.append(_with_occurrence(record.block, record.pose))
                mol.SetIntProp("input_occurrence", record.pose)
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
