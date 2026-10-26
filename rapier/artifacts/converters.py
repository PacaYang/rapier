"""Deterministic conversion and materialization of artifact representations."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Optional, Tuple

from .model import Artifact, ArtifactProvenance, ArtifactStorage
from .registry import ArtifactRegistry, get_artifact_registry

if TYPE_CHECKING:
    from .contracts import PortSpec

ConvertFunction = Callable[[Artifact, Path], Artifact]
AvailabilityFunction = Callable[[], bool]


@dataclass(frozen=True)
class ArtifactConverter:
    name: str
    source_type: str
    source_format: str
    target_type: str
    target_format: str
    convert: ConvertFunction
    priority: int = 0
    available: AvailabilityFunction = lambda: True
    provider: str = "core"


class ConverterRegistry:
    """Registry that finds and runs deterministic representation conversions."""

    def __init__(self, artifact_registry: Optional[ArtifactRegistry] = None) -> None:
        self.artifact_registry = artifact_registry or get_artifact_registry()
        self._converters: Dict[str, ArtifactConverter] = {}

    def register(self, converter: ArtifactConverter, replace: bool = False) -> None:
        if converter.name in self._converters and not replace:
            raise ValueError(f"Converter '{converter.name}' is already registered")
        self._converters[converter.name] = converter

    def list_converters(self, available_only: bool = False) -> List[str]:
        return sorted(
            name
            for name, converter in self._converters.items()
            if not available_only or converter.available()
        )

    def find_path(
        self,
        source_type: str,
        source_format: str,
        target_types: Iterable[str],
        target_formats: Iterable[str],
    ) -> Optional[List[ArtifactConverter]]:
        target_types = tuple(target_types)
        target_formats = tuple(target_formats)

        def is_target(state: Tuple[str, str]) -> bool:
            type_name, format_name = state
            type_ok = any(
                self.artifact_registry.is_compatible(type_name, expected)
                for expected in target_types
            )
            return type_ok and (not target_formats or format_name in target_formats)

        start = (source_type, source_format)
        if is_target(start):
            return []
        queue = deque([(start, [])])
        best_depth: Dict[Tuple[str, str], int] = {start: 0}
        candidates: List[List[ArtifactConverter]] = []
        candidate_depth: Optional[int] = None

        converters = sorted(
            (item for item in self._converters.values() if item.available()),
            key=lambda item: (-item.priority, item.name),
        )
        while queue:
            state, path = queue.popleft()
            if candidate_depth is not None and len(path) >= candidate_depth:
                continue
            for converter in converters:
                # Parent-type converters also apply to subtypes (poses, scores).
                if converter.source_format != state[
                    1
                ] or not self.artifact_registry.is_compatible(
                    state[0], converter.source_type
                ):
                    continue
                next_state = (
                    self._result_type(state[0], converter),
                    converter.target_format,
                )
                next_path = path + [converter]
                if is_target(next_state):
                    candidate_depth = len(next_path)
                    candidates.append(next_path)
                    continue
                if best_depth.get(next_state, len(next_path) + 1) < len(next_path):
                    continue
                best_depth[next_state] = len(next_path)
                queue.append((next_state, next_path))

        if not candidates:
            return None
        candidates.sort(
            key=lambda path: (
                len(path),
                -sum(item.priority for item in path),
                tuple(item.name for item in path),
            )
        )
        return candidates[0]

    def _result_type(self, current_type: str, converter: ArtifactConverter) -> str:
        """Type after a conversion: a subtype survives if it supports the new format."""
        if (
            converter.source_type == converter.target_type
            and current_type != converter.target_type
        ):
            if self.artifact_registry.has(current_type):
                formats = self.artifact_registry.get(current_type).formats
                if converter.target_format in formats or "*" in formats:
                    return current_type
        return converter.target_type

    def convert_for_port(
        self, artifact: Artifact, port: "PortSpec", work_dir: Path
    ) -> Artifact:
        path = self.find_path(artifact.type, artifact.format, port.types, port.formats)
        if path is None:
            providers = sorted({item.provider for item in self._converters.values()})
            raise ValueError(
                f"No available conversion from {artifact.type}/{artifact.format} to "
                f"types {list(port.types)} and formats {list(port.formats) or 'any'}; "
                f"registered providers: {providers or ['none']}"
            )
        current = artifact
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        for converter in path:
            converted = converter.convert(current, work_dir)
            expected_type = self._result_type(current.type, converter)
            if converted.type != expected_type:
                converted = Artifact(
                    expected_type,
                    converted.format,
                    converted.value,
                    storage=converted.storage,
                    metadata=dict(converted.metadata),
                )
            converted.provenance = ArtifactProvenance(
                source_artifact_ids=[current.artifact_id],
                producing_task=current.provenance.producing_task,
                output_port=current.provenance.output_port,
                conversions=current.provenance.conversions
                + [
                    {
                        "converter": converter.name,
                        "provider": converter.provider,
                        "from": f"{current.type}/{current.format}",
                        "to": f"{converted.type}/{converted.format}",
                    }
                ],
            )
            current = converted
        return current


def materialize_inline(artifact: Artifact, work_dir: Path, name: str) -> Artifact:
    """Write file-oriented inline representations into a task input directory."""
    if artifact.storage == ArtifactStorage.FILE or artifact.type == "binding_site":
        return artifact
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    suffixes = {"fasta": ".fasta", "smiles": ".smi", "json": ".json"}
    path = work_dir / f"{name}{suffixes.get(artifact.format, '.txt')}"
    if artifact.format == "fasta":
        identifier = artifact.metadata.get("name", artifact.artifact_id)
        text = str(artifact.value).strip()
        content = text + "\n" if text.startswith(">") else f">{identifier}\n{text}\n"
    elif artifact.format == "smiles":
        values = (
            artifact.value if isinstance(artifact.value, list) else [artifact.value]
        )
        content = "\n".join(str(value) for value in values) + "\n"
    elif artifact.format == "json":
        content = json.dumps(artifact.value, indent=2) + "\n"
    else:
        content = str(artifact.value)
    path.write_text(content)
    result = Artifact(
        artifact.type,
        artifact.format,
        path,
        storage=ArtifactStorage.FILE,
        metadata=dict(artifact.metadata),
    )
    result.provenance = ArtifactProvenance(
        source_artifact_ids=[artifact.artifact_id],
        producing_task=artifact.provenance.producing_task,
        output_port=artifact.provenance.output_port,
        conversions=artifact.provenance.conversions
        + [
            {
                "converter": "core.materialize_inline",
                "provider": "core",
                "from": f"inline:{artifact.format}",
                "to": f"file:{artifact.format}",
            }
        ],
    )
    return result


def _rdkit_available() -> bool:
    try:
        import rdkit  # noqa: F401

        return True
    except ImportError:
        return False


def _inline_sequence_to_fasta(artifact: Artifact, work_dir: Path) -> Artifact:
    sequence = (
        artifact.path.read_text().strip()
        if artifact.is_file
        else str(artifact.value).strip()
    )
    output_path = work_dir / f"{artifact.artifact_id}.fasta"
    identifier = artifact.metadata.get("name", artifact.artifact_id)
    output_path.write_text(
        sequence + "\n" if sequence.startswith(">") else f">{identifier}\n{sequence}\n"
    )
    return Artifact(
        artifact.type, "fasta", output_path, metadata=dict(artifact.metadata)
    )


def _convert_molecules(target_format: str) -> ConvertFunction:
    def convert(artifact: Artifact, work_dir: Path) -> Artifact:
        from rdkit import Chem

        suffix = target_format if target_format != "smiles" else "smi"
        output_path = work_dir / f"{artifact.artifact_id}.{suffix}"
        if artifact.format == "smiles":
            molecules = []
            with artifact.path.open() as handle:
                for line in handle:
                    parts = line.split()
                    if parts:
                        molecule = Chem.MolFromSmiles(parts[0])
                        if molecule is not None:
                            if len(parts) > 1:
                                molecule.SetProp("_Name", parts[1])
                            molecules.append(molecule)
        else:
            molecules = [
                mol for mol in Chem.SDMolSupplier(str(artifact.path)) if mol is not None
            ]
        if target_format == "sdf":
            writer = Chem.SDWriter(str(output_path))
            for molecule in molecules:
                writer.write(molecule)
            writer.close()
        else:
            with output_path.open("w") as handle:
                for molecule in molecules:
                    name = (
                        molecule.GetProp("_Name") if molecule.HasProp("_Name") else ""
                    )
                    handle.write(
                        f"{Chem.MolToSmiles(molecule)} {name}\n".rstrip() + "\n"
                    )
        return Artifact(
            artifact.type, target_format, output_path, metadata=dict(artifact.metadata)
        )

    return convert


def _gemmi_available() -> bool:
    try:
        import gemmi  # noqa: F401

        return True
    except ImportError:
        return False


def _convert_structure(target_format: str) -> ConvertFunction:
    def convert(artifact: Artifact, work_dir: Path) -> Artifact:
        import gemmi

        structure = gemmi.read_structure(str(artifact.path))
        suffix = "cif" if target_format == "cif" else "pdb"
        output_path = work_dir / f"{artifact.artifact_id}.{suffix}"
        if target_format == "cif":
            structure.make_mmcif_document().write_file(str(output_path))
        else:
            structure.write_pdb(str(output_path))
        return Artifact(
            artifact.type, target_format, output_path, metadata=dict(artifact.metadata)
        )

    return convert


def _obabel_available() -> bool:
    return shutil.which("obabel") is not None


def _obabel_converter(target_format: str) -> ConvertFunction:
    def convert(artifact: Artifact, work_dir: Path) -> Artifact:
        output_path = work_dir / f"{artifact.artifact_id}.{target_format}"
        subprocess.run(
            [
                "obabel",
                f"-i{artifact.format}",
                str(artifact.path),
                f"-o{target_format}",
                "-O",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return Artifact(
            artifact.type, target_format, output_path, metadata=dict(artifact.metadata)
        )

    return convert


def _convert_table(target_format: str) -> ConvertFunction:
    def convert(artifact: Artifact, work_dir: Path) -> Artifact:
        from rapier.data import load_table, write_rows

        rows = (
            load_table(artifact.path, artifact.format)
            if artifact.is_file
            else list(artifact.value)
        )
        output_path = write_rows(
            rows, work_dir / f"{artifact.artifact_id}.{target_format}", target_format
        )
        return Artifact(
            artifact.type, target_format, output_path, metadata=dict(artifact.metadata)
        )

    return convert


def create_default_converter_registry() -> ConverterRegistry:
    registry = ConverterRegistry()
    for source in ("csv", "tsv", "json"):
        for target in ("csv", "tsv", "json"):
            if source != target:
                registry.register(
                    ArtifactConverter(
                        f"core.table.{source}_to_{target}",
                        "table",
                        source,
                        "table",
                        target,
                        _convert_table(target),
                        100,
                        provider="core",
                    )
                )
    for artifact_type in ("sequence.protein", "sequence.dna", "sequence.rna"):
        registry.register(
            ArtifactConverter(
                f"core.{artifact_type}.inline_to_fasta",
                artifact_type,
                "inline",
                artifact_type,
                "fasta",
                _inline_sequence_to_fasta,
                100,
                provider="core",
            )
        )
    for artifact_type in ("molecule", "molecule.collection"):
        registry.register(
            ArtifactConverter(
                f"rdkit.{artifact_type}.smiles_to_sdf",
                artifact_type,
                "smiles",
                artifact_type,
                "sdf",
                _convert_molecules("sdf"),
                100,
                _rdkit_available,
                "rdkit",
            )
        )
        registry.register(
            ArtifactConverter(
                f"rdkit.{artifact_type}.sdf_to_smiles",
                artifact_type,
                "sdf",
                artifact_type,
                "smiles",
                _convert_molecules("smiles"),
                100,
                _rdkit_available,
                "rdkit",
            )
        )
    for artifact_type in (
        "structure.protein",
        "structure.nucleic_acid",
        "structure.complex",
    ):
        registry.register(
            ArtifactConverter(
                f"gemmi.{artifact_type}.cif_to_pdb",
                artifact_type,
                "cif",
                artifact_type,
                "pdb",
                _convert_structure("pdb"),
                100,
                _gemmi_available,
                "gemmi",
            )
        )
        registry.register(
            ArtifactConverter(
                f"gemmi.{artifact_type}.pdb_to_cif",
                artifact_type,
                "pdb",
                artifact_type,
                "cif",
                _convert_structure("cif"),
                100,
                _gemmi_available,
                "gemmi",
            )
        )
        registry.register(
            ArtifactConverter(
                f"openbabel.{artifact_type}.cif_to_pdb",
                artifact_type,
                "cif",
                artifact_type,
                "pdb",
                _obabel_converter("pdb"),
                50,
                _obabel_available,
                "openbabel",
            )
        )
    registry.register(
        ArtifactConverter(
            "openbabel.structure.protein.pdbqt_to_pdb",
            "structure.protein",
            "pdbqt",
            "structure.protein",
            "pdb",
            _obabel_converter("pdb"),
            50,
            _obabel_available,
            "openbabel",
        )
    )
    return registry


_default_converter_registry: Optional[ConverterRegistry] = None


def get_converter_registry() -> ConverterRegistry:
    global _default_converter_registry
    if _default_converter_registry is None:
        _default_converter_registry = create_default_converter_registry()
    return _default_converter_registry
