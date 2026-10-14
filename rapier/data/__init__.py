"""Typed data exchanged between methods.

Every method input and output is an :class:`~rapier.artifacts.Artifact`
subclass defined here. The class decides the semantic type string used in
pipeline YAML and the builder (``Molecules.type_name == "molecule.collection"``),
the accepted formats, inline-value validation, how raw Python values are
wrapped, and typed readers such as :meth:`Molecules.records` and
:meth:`Scores.records`.

========================  ==========================  ==============================
Class                     type string                 formats
========================  ==========================  ==============================
Sequence / Protein..      sequence[.protein|.dna|.rna] inline, fasta
Molecule                  molecule                    smiles, sdf, mol2, pdbqt, ccd
Molecules                 molecule.collection         smiles, sdf, mol2, pdbqt, ccd
Poses                     molecule.poses              sdf
Structure / Protein..     structure[.protein|...]     pdb, cif, pdbqt
BindingSite               binding_site                box, residues, reference, json
Table                     table                       csv, tsv, json
Scores                    table.scores                csv, tsv, json
Report                    report                      json, text, html, csv
File                      file                        any
========================  ==========================  ==============================

A subtype is accepted wherever its parent is (``Poses`` plug into a
``Molecules`` port, ``Scores`` into a ``Table`` port). Define a new type by
subclassing one of these with a new dotted ``type_name``.
"""

from .ids import molecule_id
from .molecules import (
    Molecule,
    MoleculeRecord,
    Molecules,
    Poses,
    parse_sdf_record,
    parse_smiles_lines,
    sdf_terminated,
    split_sdf,
)
from .sequences import DnaSequence, ProteinSequence, RnaSequence, Sequence
from .sites import BindingSite, load_box
from .structures import ComplexStructure, NucleicAcidStructure, ProteinStructure, Structure
from .tables import KNOWN_DIRECTIONS, File, Report, Scores, Table, load_table, write_rows

__all__ = [
    "Sequence", "ProteinSequence", "DnaSequence", "RnaSequence",
    "Molecule", "Molecules", "Poses", "MoleculeRecord",
    "Structure", "ProteinStructure", "NucleicAcidStructure", "ComplexStructure",
    "BindingSite", "Table", "Scores", "Report", "File",
    "molecule_id", "split_sdf", "parse_sdf_record", "parse_smiles_lines", "sdf_terminated",
    "load_box", "load_table", "write_rows", "KNOWN_DIRECTIONS",
]
