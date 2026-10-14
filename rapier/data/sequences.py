"""Biological sequence data types."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence as Formats

from ..artifacts.model import Artifact, ArtifactStorage


class Sequence(Artifact):
    """Biological sequence (inline residues or a FASTA file)."""

    type_name = "sequence"
    formats = ("inline", "fasta")
    alphabet = ""

    @classmethod
    def check_value(cls, artifact: Artifact) -> List[str]:
        if artifact.storage != ArtifactStorage.INLINE or not cls.alphabet:
            return []
        sequence = re.sub(r"\s+", "", str(artifact.value)).upper()
        if not sequence:
            return ["Inline sequence cannot be empty"]
        invalid = sorted(set(sequence) - set(cls.alphabet))
        if invalid:
            return [f"Sequence contains invalid symbols: {', '.join(invalid)}"]
        return []

    @classmethod
    def from_string(cls, sequence: str, metadata: Optional[Dict[str, Any]] = None) -> "Sequence":
        return cls.inline(re.sub(r"\s+", "", sequence), "inline", metadata)

    @classmethod
    def coerce(cls, value: Any, *, type_name: Optional[str] = None, formats: Formats = (),
               directory: Optional[Path] = None, stem: str = "data") -> Artifact:
        # A bare residue string is a sequence, not a file name.
        if isinstance(value, str) and not Path(value).suffix and not Path(value).exists():
            return cls.inline(value.strip(), "inline", type_name=type_name)
        return super().coerce(value, type_name=type_name, formats=formats, directory=directory, stem=stem)

    @property
    def sequence(self) -> str:
        """Residues of the first record, without FASTA header or whitespace."""
        text = self.path.read_text() if self.is_file else str(self.value)
        residues: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(">"):
                if residues:
                    break
                continue
            residues.append(line)
        return re.sub(r"\s+", "", "".join(residues)).upper()


class ProteinSequence(Sequence):
    """Protein amino-acid sequence."""

    type_name = "sequence.protein"
    alphabet = "ABCDEFGHIKLMNPQRSTVWXYZ*UOJ"


class DnaSequence(Sequence):
    """DNA nucleotide sequence."""

    type_name = "sequence.dna"
    alphabet = "ACGTNRYKMSWBDHV-"


class RnaSequence(Sequence):
    """RNA nucleotide sequence."""

    type_name = "sequence.rna"
    alphabet = "ACGUNRYKMSWBDHV-"
