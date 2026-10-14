"""Custom method template.

Copy this file, rename the class and ``register_name``, then fill in the
contract and ``run``. A method is a :class:`rapier.Method` subclass that:

1. Declares typed ports over the data classes in :mod:`rapier.data`
   (Molecules, Poses, Scores, ProteinStructure, BindingSite, ...).
2. Declares its settings as ``config_fields`` (validated strictly).
3. Implements ``run(inputs, ctx)``: typed inputs in, ``{port: value}`` out.

The base class formats inputs (wraps paths/SMILES/dicts as the port's data
class, converts formats, stages files) and arranges outputs (paths, row lists
and dicts become the declared output classes), so this method connects to any
other method whose ports carry the same types.

Put the file under ``rapier/methods/`` for auto-discovery, or register it
manually with ``get_registry().register("my_custom_method", MyCustomMethod)``.
"""

from typing import Any, Dict, List

from rapier import (
    ConfigField, EnvironmentSpec, Method, MethodError, Molecules, PortSpec, ProteinStructure,
    Report, ResourceSpec, RunContext, Scores,
)


class MyCustomMethod(Method):
    """One-line summary shown in the workflow builder palette.

    Longer description of the algorithm and where it fits in a screening
    workflow.

    Parameters:
        - min_heavy_atoms: Molecules with fewer heavy atoms are dropped
        - label: Free-text tag written into every score row
    """

    register_name = "my_custom_method"   # Type used in pipeline YAML
    display_name = "My Custom Method"    # Builder card title
    category = "analysis"                # Builder palette group
    version = "1.0"

    inputs = {
        # Accepting only SDF is enough: SMILES inputs are converted (RDKit) before run().
        "molecules": PortSpec(Molecules, "sdf", description="Molecules to process"),
        "reference": PortSpec(ProteinStructure, ("pdb", "cif"), required=False,
                              description="Optional reference structure"),
    }
    outputs = {
        "kept": PortSpec(Molecules, "sdf", description="Molecules that passed"),
        "scores": PortSpec(Scores, "csv", description="Per-molecule score keyed by id"),
        "summary": PortSpec(Report, "json", description="Run statistics"),
    }
    config_fields = {
        "min_heavy_atoms": ConfigField(int, default=1, minimum=0, description="Smallest molecule kept"),
        "label": ConfigField(str, default="", description="Tag written into every score row"),
    }
    resources = ResourceSpec(cpus=1, memory="2GB", time="00:30:00")
    environment = EnvironmentSpec(executables=[], python_packages=[])
    # Optional builder widget hints, e.g. {"min_heavy_atoms": {"card": True}}
    ui_hints: Dict[str, Dict[str, Any]] = {"min_heavy_atoms": {"card": True}}

    def check_config(self, config: Dict[str, Any]) -> List[str]:
        """Cross-field config errors; per-field types/ranges come from config_fields."""
        return []

    def check_inputs(self, inputs: Dict[str, Any]) -> List[str]:
        """Content checks on inputs that already satisfy the port contract."""
        return []

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        """Do the work. ``ctx.output_dir`` is where to write files."""
        molecules = inputs["molecules"]
        kept, rows = [], []
        for record in molecules.records():  # id, name, block, properties, atoms
            heavy = len(record.heavy_atoms)
            passed = heavy >= self.config["min_heavy_atoms"]
            rows.append({"id": record.id, "heavy_atoms": heavy, "passed": passed, "label": self.config["label"]})
            if passed:
                kept.append(record.block)
        if not rows:
            raise MethodError("No molecules in input")  # Fails the task with this message.

        return {
            # A data object written explicitly...
            "kept": Molecules.write_sdf(kept, ctx.output_dir / "kept.sdf"),
            # ...or plain values that the base class arranges into the port type:
            # rows become a Scores CSV, a dict becomes a JSON Report.
            "scores": Scores.from_records(rows, ctx.output_dir / "scores.csv",
                                          directions={"heavy_atoms": "higher"}),
            "summary": {"total": len(rows), "kept": len(kept)},
        }


if __name__ == "__main__":
    # Quick local check: python examples/methods/custom_method_template.py <ligands.sdf>
    import sys

    from rapier import Task

    task = Task("demo", MyCustomMethod(config={"min_heavy_atoms": 3}), inputs={"molecules": sys.argv[1]})
    status = task.method.execute(task)
    print(status.name, task.error_message or {name: str(value) for name, value in task.outputs.items()})
