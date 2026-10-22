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
* :meth:`execute` checks the runtime environment and child dependencies, then
  calls the child's :meth:`validate_outputs` once with the full output pool.
  Registered children implement both :meth:`check_dependencies` and
  :meth:`validate_outputs` after examining their input and output contracts.

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


OUTPUT_VALIDATION_VERSION = 1


@dataclass
class RunContext:
    """Where a run happens: ``output_dir`` is where ``run`` should write files."""

    task: Any
    work_dir: Path
    input_dir: Path
    output_dir: Path
    resources: Dict[str, Any] = field(default_factory=dict)
    output_errors: List[str] = field(default_factory=list)

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

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        """Method-specific prerequisites checked on the execution host before ``run``.

        Registered methods must provide a concrete implementation. Software
        declared in ``environment`` is checked separately, before this hook.
        """
        return []

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext,
    ) -> List[str]:
        """Check the complete output pool once; return every semantic error.

        Registered methods must implement this hook. ``outputs`` contains all
        independently normalized artifacts, including references to missing
        files. Handle absent/invalid siblings and aggregate read errors so one
        broken result does not hide unrelated problems.
        """
        return []

    def extract_binding_sites(self, outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract protein residues contacting predicted ligands.

        ``outputs`` may contain the raw values returned by :meth:`run` or the
        normalized artifacts produced by :meth:`format_outputs`.  The child
        method must declare a ``predicted_structures`` output containing PDB or
        CIF files.  One record is returned for every ligand residue in every
        model, with contacting protein residues identified by a strict
        ``< 5 A`` heavy-atom distance.

        Gemmi is imported lazily because structure parsing is an optional
        capability of the base package.
        """
        assert "predicted_structures" in self.outputs, (
            f"{type(self).__name__} must declare a 'predicted_structures' output"
        )
        if not isinstance(outputs, dict):
            raise TypeError("outputs must be a dictionary of method output values")
        if "predicted_structures" not in outputs:
            raise RuntimeError("outputs is missing the 'predicted_structures' value")

        structures = outputs["predicted_structures"]
        if structures is None:
            raise RuntimeError("'predicted_structures' output is empty")
        if isinstance(structures, (list, tuple)):
            structure_values = list(structures)
        else:
            structure_values = [structures]
        if not structure_values:
            raise RuntimeError("'predicted_structures' output is empty")

        paths = [self._binding_site_structure_path(value) for value in structure_values]

        try:
            import gemmi
        except ImportError as exc:
            raise RuntimeError(
                "Gemmi is required to extract binding sites from PDB/mmCIF structures; "
                "install it with 'pip install gemmi'"
            ) from exc

        records: List[Dict[str, Any]] = []
        for path in paths:
            try:
                structure = gemmi.read_structure(str(path))
            except Exception as exc:
                raise RuntimeError(f"Could not read predicted structure '{path}': {exc}") from exc
            if len(structure) == 0:
                raise RuntimeError(f"Predicted structure '{path}' contains no models")

            try:
                structure.setup_entities()
            except Exception as exc:
                raise RuntimeError(
                    f"Could not classify entities in predicted structure '{path}': {exc}"
                ) from exc

            for model in structure:
                protein_residues: List[Tuple[Any, Any]] = []
                ligand_residues: List[Tuple[Any, Any]] = []
                for chain in model:
                    for residue in chain:
                        if self._binding_site_is_protein_residue(structure, residue):
                            protein_residues.append((chain, residue))
                        elif self._binding_site_is_ligand_residue(structure, residue):
                            ligand_residues.append((chain, residue))

                model_id = str(model.name)
                if not protein_residues:
                    raise RuntimeError(
                        f"Predicted structure '{path}' model '{model_id}' contains no protein"
                    )
                if not ligand_residues:
                    raise RuntimeError(
                        f"Predicted structure '{path}' model '{model_id}' contains no ligand"
                    )

                for ligand_chain, ligand in ligand_residues:
                    contact_keys = set()
                    contacted_residues: List[Dict[str, Any]] = []
                    ligand_atoms = [atom for atom in ligand if self._binding_site_is_heavy_atom(atom)]
                    for protein_chain, protein_residue in protein_residues:
                        protein_atoms = [
                            atom for atom in protein_residue
                            if self._binding_site_is_heavy_atom(atom)
                        ]
                        if not self._binding_site_atoms_contact(protein_atoms, ligand_atoms):
                            continue
                        residue_record = self._binding_site_residue_record(protein_chain, protein_residue)
                        key = (
                            residue_record["chain_id"],
                            residue_record["residue_number"],
                            residue_record["insertion_code"],
                            residue_record["residue_name"],
                        )
                        if key not in contact_keys:
                            contact_keys.add(key)
                            contacted_residues.append(residue_record)

                    records.append({
                        "structure": str(path),
                        "model": model_id,
                        "ligand": self._binding_site_residue_record(ligand_chain, ligand),
                        "residues": contacted_residues,
                    })
        return records

    @staticmethod
    def _binding_site_structure_path(value: Any) -> Path:
        """Resolve one predicted-structure output to a supported file path."""
        if isinstance(value, Artifact):
            if not value.is_file:
                raise RuntimeError("Predicted structure artifacts must be file-backed")
            if value.format not in ("pdb", "cif", "mmcif"):
                raise RuntimeError(
                    f"Predicted structure artifact format '{value.format}' is unsupported; "
                    "expected 'pdb', 'cif', or 'mmcif'"
                )
            path = value.path
        elif isinstance(value, (str, Path)):
            path = Path(value)
            if path.suffix.lower() not in (".pdb", ".cif", ".mmcif"):
                raise RuntimeError(
                    f"Predicted structure '{path}' is unsupported; expected a .pdb, .cif, or .mmcif file"
                )
        else:
            raise TypeError(
                "Predicted structures must be file-backed artifacts or PDB/mmCIF paths"
            )
        if not path.exists():
            raise RuntimeError(f"Predicted structure file not found: {path}")
        if not path.is_file():
            raise RuntimeError(f"Predicted structure path is not a file: {path}")
        return path

    @staticmethod
    def _binding_site_entity(structure: Any, residue: Any) -> Any:
        """Return the Gemmi entity associated with a residue, if available."""
        try:
            entity = structure.get_entity(residue.subchain)
            if entity is not None:
                return entity
        except (AttributeError, TypeError):
            pass
        subchain = str(getattr(residue, "subchain", ""))
        for entity in getattr(structure, "entities", ()):
            if subchain in (str(item) for item in getattr(entity, "subchains", ())):
                return entity
        return None

    @classmethod
    def _binding_site_is_protein_residue(cls, structure: Any, residue: Any) -> bool:
        """Identify residues belonging to peptide entities, including modified AAs."""
        entity = cls._binding_site_entity(structure, residue)
        allow_name_fallback = entity is None
        if entity is not None:
            try:
                import gemmi

                if entity.polymer_type in (gemmi.PolymerType.PeptideL, gemmi.PolymerType.PeptideD):
                    return True
                if entity.polymer_type in (
                    gemmi.PolymerType.Dna,
                    gemmi.PolymerType.Rna,
                    gemmi.PolymerType.DnaRnaHybrid,
                ):
                    return False
                # Do not reinterpret an explicitly classified non-polymer
                # amino-acid-shaped residue (for example, a free amino-acid
                # ligand) as part of a protein.
                allow_name_fallback = entity.entity_type == gemmi.EntityType.Unknown
            except (AttributeError, ImportError):
                allow_name_fallback = False
        if not allow_name_fallback:
            return False
        # Entity setup can leave isolated or minimal residues unclassified.
        # A recognized amino-acid residue is the useful fallback in that case;
        # unknown/non-polymer residues remain eligible for ligand detection.
        try:
            import gemmi

            return gemmi.find_tabulated_residue(residue.name).is_amino_acid()
        except (AttributeError, ImportError):
            return False

    @classmethod
    def _binding_site_is_ligand_residue(cls, structure: Any, residue: Any) -> bool:
        """Identify molecular non-polymer residues while excluding waters/ions."""
        if cls._binding_site_is_protein_residue(structure, residue):
            return False
        try:
            import gemmi

            if residue.is_water() or gemmi.find_tabulated_residue(residue.name).is_nucleic_acid():
                return False
            entity = cls._binding_site_entity(structure, residue)
            if entity is not None and entity.entity_type == gemmi.EntityType.Water:
                return False
            # Gemmi classifies common isolated ions and buffer components as
            # BUF. Unknown/ELS residues represent molecular ligands and remain
            # eligible, including predictor-generated ATOM records.
            if gemmi.find_tabulated_residue(residue.name).kind == gemmi.ResidueKind.BUF:
                return False
        except (AttributeError, ImportError):
            return False
        return any(cls._binding_site_is_heavy_atom(atom) for atom in residue)

    @staticmethod
    def _binding_site_is_heavy_atom(atom: Any) -> bool:
        """Whether a Gemmi atom contributes to the heavy-atom distance test."""
        try:
            return int(atom.element.atomic_number) > 1
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _binding_site_atoms_contact(protein_atoms: List[Any], ligand_atoms: List[Any]) -> bool:
        """Check for any strict sub-5-A Cartesian heavy-atom contact."""
        for protein_atom in protein_atoms:
            for ligand_atom in ligand_atoms:
                dx = float(protein_atom.pos.x) - float(ligand_atom.pos.x)
                dy = float(protein_atom.pos.y) - float(ligand_atom.pos.y)
                dz = float(protein_atom.pos.z) - float(ligand_atom.pos.z)
                if dx * dx + dy * dy + dz * dz < 25.0:
                    return True
        return False

    @staticmethod
    def _binding_site_residue_record(chain: Any, residue: Any) -> Dict[str, Any]:
        """Serialize the stable residue identity used in binding-site records."""
        insertion_code = str(residue.seqid.icode).strip()
        return {
            "chain_id": str(chain.name),
            "residue_name": str(residue.name),
            "residue_number": int(residue.seqid.num),
            "insertion_code": insertion_code,
        }

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        """Do the work: typed inputs in, ``{output_port: value}`` out.

        Values may be artifacts, file paths, row lists (tables/scores), dicts
        (reports, boxes) or lists of those for ``many`` ports.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement run()")

    # ---- unified input handling -------------------------------------------

    def _items(self, port_name: str, port: PortSpec, value: Any) -> List[Any]:
        """Split a bound value into the artifacts/payloads it contains."""
        from rapier.data import Table

        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return [value]
        if port.cardinality == "many" and not value:
            return []
        if port.cardinality == "many":
            if not all(isinstance(item, dict) for item in value) or not issubclass(port.data_class, Table):
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
        """Normalize output artifacts and check their port contract (no semantic hook)."""
        outputs, errors = self._normalize_outputs(raw, ctx)
        if errors:
            raise ValueError("; ".join(errors))
        return outputs

    def _normalize_outputs(
        self, raw: Dict[str, Any], ctx: Optional[RunContext] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Gather every independent artifact before making a validation decision."""
        errors: List[str] = []
        if not isinstance(raw, dict):
            errors.append("Outputs must be a dictionary of method output values")
            raw = {}
        unknown = sorted(str(name) for name in set(raw) - set(self.output_spec.ports))
        if unknown:
            errors.append(f"Unknown output port(s): {', '.join(unknown)}")
        task_id = ctx.task.task_id if ctx else None
        directory = ctx.output_dir if ctx else None
        from rapier.methods.runner import OUTPUTS_FILE

        outputs: Dict[str, Any] = {}
        reserved_stems = set(self.output_spec.ports) | {Path(OUTPUTS_FILE).stem}
        used_stems = set()
        for name, port in self.output_spec.ports.items():
            if raw.get(name) is None:
                continue
            try:
                value = raw[name]
                if (port.cardinality == "one" and isinstance(value, (list, tuple))
                        and any(isinstance(item, (Artifact, Path)) for item in value)):
                    items = list(value)
                    if len(items) != 1:
                        errors.append(f"Output '{name}' expects one artifact, received {len(items)}")
                else:
                    items = self._items(name, port, value)
            except (TypeError, ValueError) as exc:
                errors.append(f"Output '{name}': {str(exc).replace('Input ', 'Output ', 1)}")
                # A cardinality error must not hide independent artifacts in
                # the same port from structural or pooled semantic checks.
                if isinstance(raw[name], (list, tuple)) and all(isinstance(item, Artifact) for item in raw[name]):
                    items = list(raw[name])
                else:
                    continue
            artifacts = []
            for index, item in enumerate(items):
                stem = name if len(items) == 1 else f"{name}_{index}"
                if stem == Path(OUTPUTS_FILE).stem:
                    stem += "_artifact"
                base_stem = stem
                suffix = 2
                while stem in used_stems or stem in reserved_stems and stem != name:
                    stem = f"{base_stem}_{suffix}"
                    suffix += 1
                used_stems.add(stem)
                try:
                    artifact = port.coerce(item, directory, stem)
                    if (directory is not None and artifact.is_file
                            and artifact.path.resolve() == (directory / OUTPUTS_FILE).resolve()):
                        errors.append(f"Output '{name}' item {index}: {OUTPUTS_FILE} is reserved for the validation receipt")
                    artifacts.append(dataclasses.replace(artifact, provenance=ArtifactProvenance(
                        source_artifact_ids=list(artifact.provenance.source_artifact_ids),
                        producing_task=task_id or artifact.provenance.producing_task,
                        output_port=name,
                        conversions=list(artifact.provenance.conversions),
                    )))
                except Exception as exc:
                    errors.append(f"Output '{name}' item {index}: {exc}")
            if port.cardinality == "many":
                outputs[name] = artifacts
            elif len(artifacts) > 1:
                outputs[name] = artifacts
            elif artifacts:
                outputs[name] = artifacts[0]
        _, contract_errors = self.output_spec.validate(outputs)
        errors.extend(contract_errors)
        return outputs, errors

    # ---- execution ----------------------------------------------------------

    def execute(self, task: Any) -> TaskStatus:
        """Check prerequisites, run, then validate the complete pool before publishing."""
        task.outputs = {}
        task.error_message = None
        task.metadata.pop("output_validation_version", None)
        try:
            ctx = RunContext.for_task(task)
            from rapier.methods.runner import OUTPUTS_FILE

            # Local retries invalidate scheduler receipts too. A failed new
            # attempt must never collect a previous attempt's successful pool.
            (ctx.output_dir / OUTPUTS_FILE).unlink(missing_ok=True)
            task.inputs = self.format_inputs(task.inputs, ctx.input_dir)
            valid, errors = self.validate_inputs(task.inputs)
            if not valid:
                task.error_message = f"Input validation failed: {errors}"
                return TaskStatus.FAILED
            dependency_errors: List[str] = []
            try:
                environment_valid, environment_errors = self.validate_environment()
                dependency_errors.extend(environment_errors)
                if not environment_valid and not environment_errors:
                    dependency_errors.append("Environment requirements are not satisfied")
            except Exception as exc:
                dependency_errors.append(f"Environment check failed: {exc}")
            try:
                dependency_errors.extend(self.check_dependencies(task.inputs, ctx))
            except Exception as exc:
                dependency_errors.append(f"Dependency check failed: {exc}")
            if dependency_errors:
                task.error_message = "Dependency check failed: " + "; ".join(dependency_errors)
                return TaskStatus.FAILED
            raw = self.run(task.inputs, ctx)
            outputs, errors = self._normalize_outputs({} if raw is None else raw, ctx)
            errors = list(ctx.output_errors) + errors
            try:
                errors.extend(self.validate_outputs(task.inputs, outputs, ctx))
            except Exception as exc:
                errors.append(f"Output validator failed: {exc}")
            if errors:
                task.error_message = "Output validation failed: " + "; ".join(dict.fromkeys(errors))
                return TaskStatus.FAILED
            task.outputs = outputs
            task.metadata["output_validation_version"] = OUTPUT_VALIDATION_VERSION
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
