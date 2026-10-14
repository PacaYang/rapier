"""Macromolecular structure data types."""

from ..artifacts.model import Artifact


class Structure(Artifact):
    """3D macromolecular structure (PDB, mmCIF or PDBQT)."""

    type_name = "structure"
    formats = ("pdb", "cif", "pdbqt")


class ProteinStructure(Structure):
    """Protein structure, e.g. a docking receptor."""

    type_name = "structure.protein"


class NucleicAcidStructure(Structure):
    """DNA or RNA structure."""

    type_name = "structure.nucleic_acid"
    formats = ("pdb", "cif")


class ComplexStructure(Structure):
    """Multi-entity complex, e.g. a co-folded protein-ligand prediction."""

    type_name = "structure.complex"
    formats = ("pdb", "cif")
