"""Parent class of every Rapier method.

A method is a :class:`Method` subclass that declares its contract as class
attributes and implements :meth:`Method.run`::

    class MwFilter(Method):
        register_name = "mw_filter"
        inputs = {"ligands": PortSpec(Molecules, "sdf")}
        outputs = {"kept": PortSpec(Molecules, "sdf"), "properties": PortSpec(Scores, "csv")}
        config_fields = {"mw_max": ConfigField((int, float), default=500)}

        def run(self, inputs, ctx):
            records = inputs["ligands"].records()
            ...
            return {"kept": ctx.output_dir / "kept.sdf", "properties": rows}

The base class handles the unified I/O on both sides:

* :meth:`format_inputs` wraps raw values (paths, SMILES strings, box dicts,
  upstream artifacts) as each port's data class, validates them, converts them
  to an accepted format and stages them in the task's input directory.
* :meth:`format_outputs` wraps whatever ``run`` returns (artifacts, paths,
  score rows, report dicts) as each output port's data class, validates it and
  records provenance.

Every method therefore takes and returns ``{port: Artifact}`` dictionaries of
the types in :mod:`rapier.data`, which is what makes them connect in a workflow.
"""

import copy
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rapier.artifacts import Artifact, ArtifactProvenance, ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec
from rapier.artifacts.resolver import prepare_input
from rapier.core.enums import TaskStatus
from rapier.core.specs import EnvironmentSpec, ResourceSpec


class MethodError(RuntimeError):
    """Raised by ``run`` to fail a task with a user-facing message."""


@dataclass
class RunContext:
    """Where a run happens: ``output_dir`` is where ``run`` should write files."""

    task: Any
    work_dir: Path
    input_dir: Path
    output_dir: Path
    resources: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_task(cls, task: Any) -> "RunContext":
        work_dir = Path(task.work_dir or (Path.cwd() / "runs" / task.task_id))
        task.work_dir = work_dir
        output_dir = work_dir / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_dir = Path(task.input_dir) if task.input_dir else work_dir / "input"
        return cls(task, work_dir, input_dir / "normalized", output_dir, dict(task.metadata.get("resources") or {}))
class Method:
    """Base class of all methods; subclasses declare the class attributes below.

    Identity: ``register_name`` (type used in pipeline YAML; enables
    auto-discovery), ``display_name``, ``category``, ``version``.
    Contract: ``inputs`` / ``outputs`` (``{port: PortSpec}``), ``config_fields``
    (``{name: ConfigField}``), ``resources``, ``environment`` and the optional
    workflow-builder ``ui_hints``.
    """

    register_name: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[str] = None
    version: str = "1.0"
    inputs: Dict[str, PortSpec] = {}
    outputs: Dict[str, PortSpec] = {}
    config_fields: Dict[str, ConfigField] = {}
    resources: ResourceSpec = ResourceSpec()
    environment: EnvironmentSpec = EnvironmentSpec()
    ui_hints: Dict[str, Dict[str, Any]] = {}

    def __init__(
        self,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        *,
        version: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        """``name`` is the pipeline-level id of this step; ``parameters`` is an alias of ``config``."""
        if config is not None and parameters is not None:
            raise ValueError("Pass either config or parameters, not both")
        self.name = name or self.register_name or type(self).__name__
        self.version = version or type(self).version
        self.input_spec = InputSpec(dict(self.inputs))
        self.output_spec = OutputSpec(dict(self.outputs))
        self.config_spec = ConfigSpec(dict(self.config_fields), validator=self.check_config)
        self.resource_spec = copy.deepcopy(self.resources)
        self.env_requirements = copy.deepcopy(self.environment)
        self.config = self.config_spec.normalize(self.migrate_config(dict(config or parameters or {})))

    # ---- hooks for subclasses ---------------------------------------------

    def migrate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Translate deprecated config keys before validation."""
        return config

    def check_config(self, config: Dict[str, Any]) -> List[str]:
        """Cross-field config errors (after per-field validation)."""
        return []

    def validate_bindings(self, bound_ports: Iterable[str]) -> List[str]:
        """Port requirements that depend on config, checked statically by the validator."""
        return []

    def check_inputs(self, inputs: Dict[str, Artifact]) -> List[str]:
        """Content checks on inputs that already satisfy the port contract."""
        return []

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        """Do the work: typed inputs in, ``{output_port: value}`` out.

        Values may be artifacts, file paths, row lists (tables/scores), dicts
        (reports, boxes) or lists of those for ``many`` ports.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement run()")

    # ---- unified input handling -------------------------------------------

    def _items(self, port_name: str, port: PortSpec, value: Any) -> List[Any]:
        """Split a bound value into the artifacts/payloads it contains."""
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return [value]
        if port.cardinality == "many" and not all(isinstance(item, dict) for item in value):
            return value
        if value and all(isinstance(item, Artifact) for item in value):
            if len(value) != 1:
                raise ValueError(f"Input '{port_name}' expects one artifact, received {len(value)}")
            return value
        return [value]  # One payload that happens to be a list (SMILES strings, score rows).

    def coerce_inputs(self, raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Wrap raw values as port data classes without writing files."""
        coerced: Dict[str, Any] = {}
        errors: List[str] = []
        for name, value in raw.items():
            port = self.input_spec.ports.get(name)
            if port is None or value is None:
                coerced[name] = value
                continue
            try:
                items = [port.coerce(item) for item in self._items(name, port, value)]
            except (TypeError, ValueError) as exc:
                errors.append(f"Input '{name}': {exc}")
                continue
            coerced[name] = items if port.cardinality == "many" else items[0]
        return coerced, errors

    def format_inputs(self, raw: Dict[str, Any], input_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Typed, validated and (with ``input_dir``) converted and staged inputs."""
        unknown = sorted(set(raw) - set(self.input_spec.ports))
        if unknown:
            raise ValueError(f"Unknown input port(s): {', '.join(unknown)}")
        formatted: Dict[str, Any] = {}
        for name, port in self.input_spec.ports.items():
            if raw.get(name) is None:
                continue
            prepared = []
            for index, item in enumerate(self._items(name, port, raw[name])):
                try:
                    prepared.append(prepare_input(port.coerce(item), port, input_dir, f"{name}-{index}"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Input '{name}': {exc}") from exc
            formatted[name] = prepared if port.cardinality == "many" else prepared[0]
        return formatted

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Port contract, config-dependent bindings, then :meth:`check_inputs`."""
        coerced, errors = self.coerce_inputs(inputs)
        failed = {message.split("'")[1] for message in errors}
        _, contract_errors = self.input_spec.validate(coerced)
        # A value that could not be wrapped is reported once, not also as missing.
        errors += [e for e in contract_errors if not any(e.endswith(f": {name}") for name in failed)]
        errors += self.validate_bindings(name for name, value in inputs.items() if value is not None)
        if not errors:
            errors += self.check_inputs(coerced)
        return not errors, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        return self.env_requirements.validate()

    # ---- unified output handling ------------------------------------------

    def format_outputs(self, raw: Dict[str, Any], ctx: Optional[RunContext] = None) -> Dict[str, Any]:
        """Wrap ``run`` results as output-port data classes, validate them and add provenance."""
        unknown = sorted(set(raw) - set(self.output_spec.ports))
        if unknown:
            raise ValueError(f"Unknown output port(s): {', '.join(unknown)}")
        task_id = ctx.task.task_id if ctx else None
        directory = ctx.output_dir if ctx else None
        outputs: Dict[str, Any] = {}
        for name, port in self.output_spec.ports.items():
            if raw.get(name) is None:
                continue
            items = self._items(name, port, raw[name])
            artifacts = []
            for index, item in enumerate(items):
                stem = name if len(items) == 1 else f"{name}_{index}"
                artifact = port.coerce(item, directory, stem)
                artifacts.append(dataclasses.replace(artifact, provenance=ArtifactProvenance(
                    source_artifact_ids=list(artifact.provenance.source_artifact_ids),
                    producing_task=task_id or artifact.provenance.producing_task,
                    output_port=name,
                    conversions=list(artifact.provenance.conversions),
                )))
            outputs[name] = artifacts if port.cardinality == "many" else artifacts[0]
        valid, errors = self.output_spec.validate(outputs)
        if not valid:
            raise ValueError("; ".join(errors))
        return outputs

    # ---- execution ----------------------------------------------------------

    def execute(self, task: Any) -> TaskStatus:
        """Run in-process: format inputs, ``run``, format outputs into ``task.outputs``."""
        try:
            ctx = RunContext.for_task(task)
            task.inputs = self.format_inputs(task.inputs, ctx.input_dir)
            valid, errors = self.validate_inputs(task.inputs)
            if not valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED
            task.outputs = self.format_outputs(self.run(task.inputs, ctx) or {}, ctx)
            return TaskStatus.COMPLETED
        except Exception as exc:
            task.error_message = f"{self.display_name or self.name} failed: {exc}"
            return TaskStatus.FAILED

    def get_command(self, task: Any) -> str:
        """Scheduler command; defaults to the generic :mod:`rapier.methods.runner`."""
        from rapier.methods.runner import runner_command

        return runner_command(self, task)

    def collect_outputs(self, task: Any) -> Dict[str, Any]:
        """Outputs after a scheduler run (``outputs/outputs.json``) or a local execute."""
        from rapier.methods.runner import read_outputs

        return read_outputs(task)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name}, version={self.version})"
