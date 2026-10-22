"""Custom method template.

Copy this file, rename the class and ``register_name``, then fill in the
contract and ``run``. A method is a :class:`rapier.Method` subclass that:

1. Declares typed ports over the data classes in :mod:`rapier.data`
   (Molecules, Poses, Scores, ProteinStructure, BindingSite, ...).
2. Declares its settings as ``config_fields`` (validated strictly).
3. Implements ``check_dependencies(inputs, ctx)`` before execution,
   ``run(inputs, ctx)`` and ``validate_outputs(inputs, outputs, ctx)`` once
   after all output ports have been pooled.

The base class formats inputs (wraps paths/SMILES/dicts as the port's data
class, converts formats, stages files) and arranges outputs (paths, row lists
and dicts become the declared output classes), so this method connects to any
other method whose ports carry the same types.

Put the file under ``rapier/methods/`` for auto-discovery, or register it
manually with ``get_registry().register("my_custom_method", MyCustomMethod)``.
"""

from collections import Counter
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

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        """Check execution prerequisites after examining the declared input contract."""
        try:
            return [] if inputs["molecules"].records() else ["No molecules in input"]
        except (OSError, ValueError, UnicodeError) as exc:
            return [f"Cannot read molecules: {exc}"]

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Compare all result inventories together, collecting independent errors."""
        errors, pooled = [], {}
        readers = {"kept": lambda value: value.records(),
                   "summary": lambda value: value.load()}
        for port, reader in readers.items():
            try:
                pooled[port] = reader(outputs[port])
            except (KeyError, AttributeError, OSError, ValueError, UnicodeError) as exc:
                errors.append(f"Cannot read {port}: {exc}")
        try:
            pooled["scores"], row_errors = outputs["scores"].rows_with_errors()
            errors.extend(f"Cannot read scores: {error}" for error in row_errors)
        except (KeyError, AttributeError) as exc:
            errors.append(f"Cannot read scores: {exc}")
        rows = pooled.get("scores")
        if rows is not None:
            expected = Counter((record.id, record.pose) for record in inputs["molecules"].records())
            actual = Counter()
            passed = []
            for index, row in enumerate(rows, 1):
                try:
                    actual[(str(row["id"]), int(row["occurrence"]))] += 1
                    if str(row["passed"]).lower() not in ("true", "false"):
                        raise ValueError("passed must be true or false")
                    if str(row["passed"]).lower() == "true":
                        passed.append(str(row["id"]))
                except (KeyError, TypeError, ValueError) as exc:
                    errors.append(f"Invalid score row {index}: {exc}")
            if actual != expected:
                errors.append("Scores must cover every input occurrence exactly once")
            if "kept" in pooled and Counter(record.id for record in pooled["kept"]) != Counter(passed):
                errors.append("Kept molecules must match the passing scores")
            if "summary" in pooled:
                summary = pooled["summary"]
                if not isinstance(summary, dict) or summary.get("total") != len(rows) or summary.get("kept") != len(passed):
                    errors.append("Summary counts must match the complete score inventory")
        return errors

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        """Do the work. ``ctx.output_dir`` is where to write files."""
        molecules = inputs["molecules"]
        kept, rows = [], []
        for record in molecules.records():  # id, name, block, properties, atoms
            heavy = len(record.heavy_atoms)
            passed = heavy >= self.config["min_heavy_atoms"]
            rows.append({"id": record.id, "occurrence": record.pose,
                         "heavy_atoms": heavy, "passed": passed, "label": self.config["label"]})
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
