"""Boltz-2 input generation, execution, and result collection.

Boltz consumes a YAML description of a biomolecular complex rather than a
single structure file.  This module translates Rapier's normalized artifacts
to that YAML and runs the official ``boltz predict`` CLI.  Keeping the
translation here makes local execution and SLURM execution identical and
keeps the method class focused on contracts and lifecycle hooks.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml

from ...artifacts import Artifact
from ..msa import protein_records, read_text as read_msa_text, resolve_msas

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(value: str, fallback: str = "sample") -> str:
    value = _SAFE_NAME.sub("_", str(value or "").strip()).strip("._")
    return value or fallback


def _flatten_numeric(value: Any) -> List[float]:
    """Collect finite numeric values from a JSON value."""
    numbers: List[float] = []
    if isinstance(value, bool):
        return numbers
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return numbers
        if number == number and abs(number) != float("inf"):
            numbers.append(number)
    elif isinstance(value, dict):
        for item in value.values():
            numbers.extend(_flatten_numeric(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers.extend(_flatten_numeric(item))
    return numbers


def _confidence_score(path: Path) -> Optional[float]:
    """Read a scalar confidence/ranking score from a Boltz JSON report."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    preferred = (
        "confidence_score",
        "confidence",
        "ranking_score",
        "complex_plddt",
        "iptm",
        "ptm",
        "pTMscore",
        "complex_score",
    )
    for key in preferred:
        if key in payload:
            values = _flatten_numeric(payload[key])
            if values:
                return sum(values) / len(values)
    values = _flatten_numeric(payload)
    return sum(values) / len(values) if values else None


def _matching_confidence(structure: Path, reports: Sequence[Path]) -> Optional[float]:
    """Find the confidence report associated with one predicted structure."""
    if not reports:
        return None
    stem = structure.stem.lower()
    candidates = []
    for report in reports:
        report_stem = report.stem.lower()
        if report_stem.startswith("confidence_"):
            report_stem = report_stem[len("confidence_") :]
        if report_stem in stem or stem in report_stem:
            candidates.append(report)
    if len(candidates) == 1:
        return _confidence_score(candidates[0])
    if len(reports) == 1:
        return _confidence_score(reports[0])
    return None


def _select_structure(
    structures: Sequence[Path], reports: Sequence[Path]
) -> Tuple[Path, Optional[float], bool]:
    """Select the highest-confidence structure with deterministic fallback."""
    scored = [(path, _matching_confidence(path, reports)) for path in structures]
    usable = [(path, score) for path, score in scored if score is not None]
    if usable:
        # Stable path ordering breaks ties reproducibly.
        selected, score = sorted(usable, key=lambda item: (-item[1], str(item[0])))[0]
        return selected, score, False
    return sorted(structures, key=lambda path: str(path))[0], None, True


def _ligand_coordinates(
    structure: Any, ligand_ids: Sequence[str]
) -> List[Tuple[float, float, float]]:
    """Extract all atom coordinates from the selected model's ligand chains."""
    wanted = {str(item) for item in ligand_ids}
    coordinates: List[Tuple[float, float, float]] = []
    if not wanted or len(structure) == 0:
        return coordinates
    model = structure[0]
    for chain in model:
        if str(chain.name) not in wanted:
            continue
        for residue in chain:
            for atom in residue:
                coordinates.append(
                    (float(atom.pos.x), float(atom.pos.y), float(atom.pos.z))
                )
    return coordinates


def _write_docking_inputs(
    structure_path: Path,
    output_dir: Path,
    ligand_ids: Sequence[str],
    padding: float,
    selected_score: Optional[float],
    fallback_selection: bool,
) -> Dict[str, Any]:
    """Create a receptor-only PDB and combined ligand-centered Vina box."""
    try:
        import gemmi
    except ImportError as exc:
        raise RuntimeError(
            "Gemmi is required to derive Vina receptor and binding box outputs"
        ) from exc

    structure = gemmi.read_structure(str(structure_path))
    coordinates = _ligand_coordinates(structure, ligand_ids)
    if not coordinates:
        raise RuntimeError(
            "Selected Boltz structure contains no atoms for ligand chain IDs "
            f"{list(ligand_ids)}"
        )
    minimum = [min(point[index] for point in coordinates) for index in range(3)]
    maximum = [max(point[index] for point in coordinates) for index in range(3)]
    center = [(minimum[index] + maximum[index]) / 2.0 for index in range(3)]
    # Vina requires strictly positive search dimensions.  A single-atom
    # ligand with zero configured padding would otherwise produce a degenerate
    # box, so retain a minimal one-Ångström extent on each axis.
    size = [
        max(maximum[index] - minimum[index] + 2.0 * float(padding), 1.0)
        for index in range(3)
    ]

    # The selected structure is no longer needed after extracting ligand
    # coordinates, so mutate it in place.  This also keeps compatibility with
    # Gemmi releases whose Structure objects are not pickle/deepcopyable.
    receptor = structure
    ligand_set = {str(item) for item in ligand_ids}
    for model in receptor:
        for chain_id in sorted(ligand_set):
            model.remove_chain(chain_id)
    if len(receptor) == 0 or all(len(model) == 0 for model in receptor):
        raise RuntimeError("Removing ligand chains left no receptor structure")
    output_dir.mkdir(parents=True, exist_ok=True)
    receptor_path = output_dir / "vina_receptor.pdb"
    receptor.write_pdb(str(receptor_path))
    return {
        "docking_receptor": str(receptor_path),
        "docking_box": {"center": center, "size": size},
        "docking_box_padding": float(padding),
        "selected_structure": str(structure_path),
        "selected_confidence": selected_score,
        "selection_fallback": bool(fallback_selection),
        "ligand_chain_ids": list(ligand_ids),
        "ligand_bounds": {"min": minimum, "max": maximum},
    }


def _artifact_list(value: Any) -> List[Artifact]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    if not all(isinstance(item, Artifact) for item in values):
        raise TypeError("Boltz inputs must be normalized Artifact objects")
    return values


def _read_text(artifact: Artifact) -> str:
    if artifact.is_file:
        return artifact.path.read_text()
    if artifact.format == "json":
        return json.dumps(artifact.value)
    return str(artifact.value)


def _fasta_records(text: str) -> List[Tuple[Optional[str], str]]:
    lines = text.splitlines()
    records: List[Tuple[Optional[str], str]] = []
    header: Optional[str] = None
    sequence: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if sequence:
                records.append((header, "".join(sequence)))
            header = line[1:].split()[0] or None
            sequence = []
        else:
            sequence.append(line)
    if sequence:
        records.append((header, "".join(sequence)))
    return records or [(None, "".join(lines).replace(" ", ""))]


def _split_sdf(text: str) -> List[str]:
    return [
        record.strip() + "\n$$$$\n" for record in text.split("$$$$") if record.strip()
    ]


def _sdf_to_smiles(text: str) -> List[Tuple[Optional[str], str]]:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise RuntimeError(
            "Boltz ligand SDF inputs require the optional RDKit package"
        ) from exc
    records: List[Tuple[Optional[str], str]] = []
    for record in _split_sdf(text):
        molecule = Chem.MolFromMolBlock(record, sanitize=True, removeHs=False)
        if molecule is None:
            continue
        name = molecule.GetProp("_Name") if molecule.HasProp("_Name") else None
        records.append((name, Chem.MolToSmiles(Chem.RemoveHs(molecule))))
    return records


def _next_chain_id(used: Set[str]) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    index = len(used)
    while True:
        if index < len(alphabet):
            candidate = alphabet[index]
        else:
            quotient, remainder = divmod(index, len(alphabet))
            candidate = alphabet[remainder] + str(quotient)
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def _metadata_id(
    artifact: Artifact, used: Set[str], fallback: Optional[str] = None
) -> str:
    requested = artifact.metadata.get("chain_id", artifact.metadata.get("id", fallback))
    if isinstance(requested, (list, tuple)):
        requested = requested[0] if requested else None
    candidate = _safe_name(str(requested), "") if requested else ""
    if not candidate or candidate in used:
        return _next_chain_id(used)
    used.add(candidate)
    return candidate


def _entity_id(value: Any, used: Set[str], fallback: str) -> Any:
    if isinstance(value, (list, tuple)):
        ids = []
        for item in value:
            item = _safe_name(str(item), "")
            if not item or item in used:
                item = _next_chain_id(used)
            else:
                used.add(item)
            ids.append(item)
        return ids
    if value:
        candidate = _safe_name(str(value), fallback)
        if candidate not in used:
            used.add(candidate)
            return candidate
    return _next_chain_id(used)


def _record_chain_id(
    artifact: Artifact,
    index: int,
    record_name: Optional[str],
    used: Set[str],
) -> str:
    metadata_ids = artifact.metadata.get("chain_id", artifact.metadata.get("id"))
    if isinstance(metadata_ids, (list, tuple)) and index < len(metadata_ids):
        requested = metadata_ids[index]
    elif metadata_ids and not isinstance(metadata_ids, (list, tuple)) and index == 0:
        requested = metadata_ids
    else:
        requested = record_name
    return _entity_id(requested, used, f"ligand_{index}")


def _load_msa_map(
    msas: Sequence[Artifact],
    records: Sequence[Dict[str, Any]],
    work_dir: Path,
) -> Dict[str, str]:
    """Resolve and stage the unified MSA port for every protein chain.

    ``resolve_msas`` performs the contract checks (exact chain coverage and
    query/sequence validation).  This function only converts inline artifacts
    to files because Boltz's YAML interface requires paths.
    """
    resolved = resolve_msas(msas, records)
    result: Dict[str, str] = {}
    for index, (chain_id, artifact) in enumerate(resolved.items()):
        if artifact.is_file:
            result[str(chain_id)] = str(artifact.path)
            continue
        msa_path = (
            work_dir / f"msa_{_safe_name(chain_id, f'msa_{index}')}.{artifact.format}"
        )
        msa_path.write_text(read_msa_text(artifact))
        result[str(chain_id)] = str(msa_path)
    return result


def _resolved_protein_entities(
    destination: List[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    msa_map: Dict[str, str],
    used: Set[str],
    artifacts: Sequence[Artifact] = (),
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Write one Boltz protein entity for each canonical chain.

    ``msa_map`` represents the authoritative unified ``msa`` port. When it
    is empty, per-artifact metadata and configured fallbacks retain the
    historical behavior while using stable chain IDs.
    """
    config = config or {}
    metadata_by_artifact = {artifact.artifact_id: artifact for artifact in artifacts}
    for index, record in enumerate(records):
        chain_id = str(record.get("chain_id") or "")
        sequence = str(record.get("sequence") or "")
        if not chain_id or not sequence:
            raise ValueError(
                f"Protein MSA record {index} is missing chain_id or sequence"
            )
        # The shared resolver has already rejected duplicate chain IDs.  Keep
        # this guard because a collision with a non-protein entity would make
        # the generated Boltz document ambiguous.
        if chain_id in used:
            raise ValueError(f"Duplicate Boltz chain ID '{chain_id}'")
        used.add(chain_id)
        msa_path = msa_map.get(chain_id)
        source_artifact = metadata_by_artifact.get(record.get("source_artifact_id"))
        source_msa = source_artifact.metadata.get("msa") if source_artifact else None
        if msa_path:
            pass
        elif source_msa:
            msa_path = str(source_msa)
        elif config.get("use_msa_server", True):
            pass
        elif config.get("single_sequence_mode"):
            msa_path = "empty"
        else:
            raise ValueError(f"Protein chain '{chain_id}' has no MSA supplied")
        protein: Dict[str, Any] = {"id": chain_id, "sequence": sequence}
        if msa_path:
            protein["msa"] = msa_path
        if source_artifact is not None:
            if source_artifact.metadata.get("modifications"):
                protein["modifications"] = source_artifact.metadata["modifications"]
            if source_artifact.metadata.get("cyclic") is not None:
                protein["cyclic"] = bool(source_artifact.metadata["cyclic"])
        destination.append({"protein": protein})


def _add_polymer_entities(
    destination: List[Dict[str, Any]],
    artifacts: Sequence[Artifact],
    entity_type: str,
    used: Set[str],
    msa_map: Dict[str, str],
    config: Dict[str, Any],
) -> None:
    for artifact in artifacts:
        records = _fasta_records(_read_text(artifact))
        metadata_ids = artifact.metadata.get("chain_id", artifact.metadata.get("id"))
        identical_ids = artifact.metadata.get("chain_ids")
        for index, (header, sequence) in enumerate(records):
            if not sequence:
                continue
            if identical_ids and len(records) == 1:
                chain_id = _entity_id(identical_ids, used, f"chain_{index}")
            elif isinstance(metadata_ids, (list, tuple)) and index < len(metadata_ids):
                chain_id = _entity_id(metadata_ids[index], used, f"chain_{index}")
            elif metadata_ids and not isinstance(metadata_ids, (list, tuple)):
                chain_id = _entity_id(metadata_ids, used, f"chain_{index}")
            elif header:
                chain_id = _entity_id(header, used, f"chain_{index}")
            else:
                chain_id = _metadata_id(artifact, used, f"chain_{index}")
            entity: Dict[str, Any] = {
                entity_type: {
                    "id": chain_id,
                    "sequence": sequence,
                }
            }
            source_msa = artifact.metadata.get("msa")
            msa_lookup_id = (
                chain_id[0] if isinstance(chain_id, list) and chain_id else chain_id
            )
            msa = source_msa or msa_map.get(str(msa_lookup_id))
            if entity_type == "protein":
                if msa:
                    entity[entity_type]["msa"] = str(msa)
                elif config.get("use_msa_server", True):
                    pass
                elif config.get("single_sequence_mode"):
                    entity[entity_type]["msa"] = "empty"
                else:
                    raise ValueError(
                        f"Protein chain '{chain_id}' has no MSA; enable "
                        "use_msa_server, "
                        "provide an MSA artifact, or set single_sequence_mode"
                    )
            if artifact.metadata.get("modifications"):
                entity[entity_type]["modifications"] = artifact.metadata[
                    "modifications"
                ]
            if artifact.metadata.get("cyclic") is not None:
                entity[entity_type]["cyclic"] = bool(artifact.metadata["cyclic"])
            destination.append(entity)


def _add_ligand_entities(
    destination: List[Dict[str, Any]],
    artifacts: Sequence[Artifact],
    used: Set[str],
) -> None:
    for artifact in artifacts:
        ccd = artifact.metadata.get("ccd")
        if artifact.format == "ccd" and not ccd:
            ccd = _read_text(artifact).strip()
        if ccd:
            chain_id = _metadata_id(artifact, used, "ligand")
            destination.append({"ligand": {"id": chain_id, "ccd": str(ccd)}})
            continue
        if artifact.format == "smiles":
            values = artifact.value if not artifact.is_file else _read_text(artifact)
            lines = values if isinstance(values, list) else str(values).splitlines()
            for index, line in enumerate(lines):
                fields = str(line).split()
                if not fields:
                    continue
                smiles, name = fields[0], (fields[1] if len(fields) > 1 else None)
                chain_id = _record_chain_id(artifact, index, name, used)
                destination.append({"ligand": {"id": chain_id, "smiles": smiles}})
            continue
        if artifact.format == "sdf":
            for index, (name, smiles) in enumerate(
                _sdf_to_smiles(_read_text(artifact))
            ):
                chain_id = _record_chain_id(artifact, index, name, used)
                destination.append({"ligand": {"id": chain_id, "smiles": smiles}})
            continue
        raise ValueError(
            f"Boltz ligands require smiles, sdf, or CCD metadata; received "
            f"{artifact.format}"
        )


def build_input_yaml(
    inputs: Dict[str, Any],
    config: Dict[str, Any],
    output_path: Path,
) -> Dict[str, Any]:
    """Translate normalized Rapier artifacts into a Boltz prediction YAML."""
    sequences: List[Dict[str, Any]] = []
    used: Set[str] = set()
    proteins = _artifact_list(inputs.get("proteins"))
    msas = _artifact_list(inputs.get("msa"))
    msa_map: Dict[str, str] = {}
    if msas:
        if not proteins:
            raise ValueError("Boltz MSA input requires at least one protein sequence")
        records = protein_records(proteins)
        msa_map = _load_msa_map(msas, records, output_path.parent)
        # A supplied MSA is authoritative.  Resolve and emit every protein
        # chain independently so homomers can carry distinct chain-tagged
        # alignments from ColabFold.
        _resolved_protein_entities(sequences, records, msa_map, used, proteins, config)
    else:
        # No unified MSA was supplied: retain Boltz's historical metadata MSA,
        # server, and single-sequence behavior unchanged, with canonical IDs.
        records = protein_records(proteins) if proteins else []
        _resolved_protein_entities(sequences, records, {}, used, proteins, config)
    _add_polymer_entities(
        sequences, _artifact_list(inputs.get("dna")), "dna", used, msa_map, config
    )
    _add_polymer_entities(
        sequences, _artifact_list(inputs.get("rna")), "rna", used, msa_map, config
    )
    _add_ligand_entities(sequences, _artifact_list(inputs.get("ligands")), used)
    if not sequences:
        raise ValueError(
            "Boltz requires at least one protein, nucleic-acid, or ligand entity"
        )

    document: Dict[str, Any] = {"version": 1, "sequences": sequences}
    constraints = config.get("constraints")
    constraint_artifacts = _artifact_list(inputs.get("constraints"))
    if constraint_artifacts:
        if len(constraint_artifacts) > 1:
            raise ValueError("Boltz accepts one constraints JSON artifact")
        constraint_text = _read_text(constraint_artifacts[0])
        loaded_constraints = yaml.safe_load(constraint_text)
        if isinstance(loaded_constraints, dict) and "constraints" in loaded_constraints:
            loaded_constraints = loaded_constraints["constraints"]
        if not isinstance(loaded_constraints, list):
            raise ValueError("Boltz constraints artifact must contain a list")
        constraints = loaded_constraints
    if constraints:
        document["constraints"] = constraints
    properties = config.get("properties")
    if properties:
        document["properties"] = properties
    affinity_binder = config.get("affinity_binder")
    if affinity_binder:
        affinity_property = {"affinity": {"binder": affinity_binder}}
        if isinstance(document.get("properties"), list):
            document["properties"] = list(document["properties"]) + [affinity_property]
        else:
            document["properties"] = [affinity_property]

    # A supplied unified MSA disables template handling for the Boltz job.  In
    # particular, do not inspect or stage stale template artifacts: the MSA
    # port has higher priority than every template-related input.
    if not msas:
        templates = []
        for artifact in _artifact_list(inputs.get("templates")):
            if not artifact.is_file:
                raise ValueError(
                    "Boltz templates must be file-backed structure artifacts"
                )
            entry: Dict[str, Any] = {
                "cif" if artifact.format == "cif" else "pdb": str(artifact.path)
            }
            for key in ("chain_id", "template_id", "force", "threshold"):
                if key in artifact.metadata:
                    entry[key] = artifact.metadata[key]
            templates.append(entry)
        if templates:
            document["templates"] = templates

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(document, sort_keys=False))
    return document


def _run_boltz(command: List[str], cwd: Path) -> Tuple[int, str, str]:
    # Boltz inference can legitimately run longer than Rapier's generic
    # one-hour utility timeout, especially on CPU or with many diffusion
    # samples.  Let SLURM's wall-time limit govern the process instead.
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return -1, "", str(exc)


def _flag(command: List[str], name: str, value: Any) -> None:
    if value is None or value is False:
        return
    if value is True:
        command.append(name)
    else:
        command.extend([name, str(value)])


def run_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Run ``boltz predict`` and summarize its deterministic output tree."""
    manifest = json.loads(Path(manifest_path).read_text())
    work_dir = Path(manifest["work_dir"])
    input_yaml = Path(manifest["input_yaml"])
    output_dir = Path(manifest["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    config = dict(manifest["config"])

    # The unified ``msa`` port is authoritative.  ``prepare`` records this
    # explicitly so a complete supplied alignment cannot trigger a remote
    # search or single-sequence fallback.  The YAML inspection keeps direct
    # runner callers backwards-compatible when they provide the marker only
    # implicitly through an all-protein MSA document.
    msa_marker = manifest.get("msa_supplied")
    supplied_msa = bool(msa_marker) if msa_marker is not None else False
    input_document = None
    if msa_marker is None:
        try:
            input_document = yaml.safe_load(input_yaml.read_text())
            protein_entities = [
                item.get("protein")
                for item in (input_document or {}).get("sequences", [])
                if isinstance(item, dict) and isinstance(item.get("protein"), dict)
            ]
            supplied_msa = bool(protein_entities) and all(
                isinstance(entity.get("msa"), str) and entity.get("msa")
                for entity in protein_entities
            )
        except Exception:
            supplied_msa = False

    if supplied_msa:
        # Enforce MSA precedence for direct runner callers as well as the
        # normal Method.prepare path.  The latter already omits templates,
        # while a hand-written manifest may still contain a top-level block.
        try:
            if input_document is None:
                input_document = yaml.safe_load(input_yaml.read_text())
            if isinstance(input_document, dict) and "templates" in input_document:
                input_document.pop("templates", None)
                input_yaml.write_text(yaml.safe_dump(input_document, sort_keys=False))
        except Exception as exc:
            raise RuntimeError(f"Could not apply supplied-MSA template override: {exc}")

    command = ["boltz", "predict", str(input_yaml), "--out_dir", str(output_dir)]
    for key, option in (
        ("cache", "--cache"),
        ("checkpoint", "--checkpoint"),
        ("devices", "--devices"),
        ("accelerator", "--accelerator"),
        ("recycling_steps", "--recycling_steps"),
        ("sampling_steps", "--sampling_steps"),
        ("diffusion_samples", "--diffusion_samples"),
        ("max_parallel_samples", "--max_parallel_samples"),
        ("step_scale", "--step_scale"),
        ("num_workers", "--num_workers"),
        ("preprocessing_threads", "--preprocessing-threads"),
        ("method", "--method"),
        ("affinity_mw_correction", "--affinity_mw_correction"),
        ("sampling_steps_affinity", "--sampling_steps_affinity"),
        ("diffusion_samples_affinity", "--diffusion_samples_affinity"),
        ("affinity_checkpoint", "--affinity_checkpoint"),
        ("max_msa_seqs", "--max_msa_seqs"),
        ("num_subsampled_msa", "--num_subsampled_msa"),
        ("output_format", "--output_format"),
    ):
        _flag(command, option, config.get(key))
    for key, option in (
        ("override", "--override"),
        ("use_msa_server", "--use_msa_server"),
        ("use_potentials", "--use_potentials"),
        ("write_full_pae", "--write_full_pae"),
        ("write_full_pde", "--write_full_pde"),
        ("subsample_msa", "--subsample_msa"),
        ("no_kernels", "--no_kernels"),
    ):
        value = config.get(key)
        if supplied_msa and key == "use_msa_server":
            value = False
        _flag(command, option, value)
    if config.get("use_msa_server") and not supplied_msa:
        _flag(command, "--msa_server_url", config.get("msa_server_url"))
        _flag(command, "--msa_pairing_strategy", config.get("msa_pairing_strategy"))

    returncode, stdout, stderr = _run_boltz(command, work_dir)
    if returncode != 0:
        raise RuntimeError(f"Boltz prediction failed: {stderr or stdout}")

    sample_name = manifest["sample_name"]

    # Boltz releases have used both ``<out_dir>/predictions/<sample>`` and
    # ``<out_dir>/boltz_results_<sample>/predictions/<sample>``.  Discover a
    # directory by its model files so Rapier can consume either layout.
    def _has_structure_files(path: Path) -> bool:
        return any(
            list(path.glob(pattern))
            for pattern in ("*_model_*.cif", "*_model_*.mmcif", "*_model_*.pdb")
        )

    prediction_dir = output_dir / "predictions" / sample_name
    if not prediction_dir.exists() or not _has_structure_files(prediction_dir):
        candidates = [
            path
            for path in output_dir.glob("**/predictions/*")
            if path.is_dir() and _has_structure_files(path)
        ]
        matching = [path for path in candidates if path.name == sample_name]
        if len(matching) == 1:
            prediction_dir = matching[0]
        elif len(candidates) == 1:
            prediction_dir = candidates[0]
    structures = (
        sorted(
            list(prediction_dir.glob("*_model_*.cif"))
            + list(prediction_dir.glob("*_model_*.mmcif"))
            + list(prediction_dir.glob("*_model_*.pdb"))
        )
        if prediction_dir.exists()
        else []
    )
    confidence = (
        sorted(prediction_dir.glob("confidence_*.json"))
        if prediction_dir.exists()
        else []
    )
    affinity = (
        sorted(prediction_dir.glob("affinity_*.json"))
        if prediction_dir.exists()
        else []
    )
    arrays = sorted(prediction_dir.glob("*.npz")) if prediction_dir.exists() else []
    if not structures:
        raise RuntimeError("Boltz completed without producing predicted structures")

    summary = {
        "status": "completed",
        "prediction_dir": str(prediction_dir),
        "structures": [str(path) for path in structures],
        "confidence": [str(path) for path in confidence],
        "affinity": [str(path) for path in affinity],
        "arrays": [str(path) for path in arrays],
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }
    if config.get("emit_docking_inputs"):
        mapping = manifest.get("entity_mapping", {})
        ligand_ids = [str(item) for item in mapping.get("ligand_ids", []) if item]
        if not ligand_ids:
            raise RuntimeError(
                "emit_docking_inputs is enabled but the Boltz manifest has no "
                "ligand chain IDs"
            )
        selected, selected_score, fallback = _select_structure(structures, confidence)
        summary.update(
            _write_docking_inputs(
                selected,
                work_dir / "outputs",
                ligand_ids,
                float(config.get("docking_box_padding", 8.0)),
                selected_score,
                fallback,
            )
        )
    summary_path = work_dir / "outputs" / "boltz2_run.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Rapier Boltz-2 manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run_manifest(args.manifest)
    except Exception as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
