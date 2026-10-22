"""Protein binding sites extracted from predicted PDB/mmCIF structures."""

import builtins

import pytest

from rapier.core.method import Method
from rapier.core.specs import PortSpec
from rapier.data import ComplexStructure


class PredictComplex(Method):
    outputs = {
        "predicted_structures": PortSpec(
            ComplexStructure, ("pdb", "cif"), cardinality="many"
        )
    }


@pytest.fixture
def gemmi():
    return pytest.importorskip("gemmi")


def _residue(chain, name, number, atoms, *, insertion="", het="A"):
    return chain, name, number, insertion, het, atoms


def _protein(x=0, chain="A"):
    return [
        _residue(chain, "ALA", 1, [("CA", "C", x, 0, 0)]),
        _residue(chain, "GLY", 2, [("CA", "C", x + 50, 0, 0)]),
    ]


def _ligand(x=1, chain="B", number=1, name="LIG", het="H"):
    return _residue(
        chain, name, number,
        [("C1", "C", x, 0, 0), ("O1", "O", x + 1, 0, 0)],
        het=het,
    )


def _write_structure(gemmi, path, models):
    """Create real structures through Gemmi, keeping chain and residue order."""
    structure = gemmi.Structure()
    structure.name = "binding-sites"
    for model_number, residues in enumerate(models, 1):
        model = gemmi.Model(str(model_number))
        chains = {}
        for chain_id, name, number, insertion, het, atoms in residues:
            chain = chains.setdefault(chain_id, gemmi.Chain(chain_id))
            residue = gemmi.Residue()
            residue.name = name
            residue.seqid = gemmi.SeqId(number, insertion or " ")
            residue.het_flag = het
            for atom_name, element, x, y, z in atoms:
                atom = gemmi.Atom()
                atom.name = atom_name
                atom.element = gemmi.Element(element)
                atom.pos = gemmi.Position(x, y, z)
                residue.add_atom(atom)
            chain.add_residue(residue)
        for chain in chains.values():
            model.add_chain(chain)
        structure.add_model(model)
    if path.suffix.lower() in (".cif", ".mmcif"):
        structure.setup_entities()
        structure.make_mmcif_document().write_file(str(path))
    else:
        structure.write_pdb(str(path))
    return path


def _identity(chain, name, number, insertion=""):
    return {
        "chain_id": chain,
        "residue_name": name,
        "residue_number": number,
        "insertion_code": insertion,
    }


def test_requires_declared_predicted_structures_port():
    with pytest.raises(AssertionError, match="predicted_structures"):
        Method().extract_binding_sites({"predicted_structures": []})


@pytest.mark.parametrize("suffix", [".pdb", ".cif", ".mmcif"])
@pytest.mark.parametrize("representation", ["path", "path_list", "normalized"])
def test_reads_paths_and_normalized_artifacts(gemmi, tmp_path, suffix, representation):
    path = _write_structure(gemmi, tmp_path / ("complex" + suffix), [
        _protein() + [_ligand()]
    ])
    method = PredictComplex()
    outputs = {"predicted_structures": path if representation == "path" else [str(path)]}
    if representation == "normalized":
        outputs = method.format_outputs(outputs)

    assert method.extract_binding_sites(outputs) == [{
        "structure": str(path),
        "model": "1",
        "ligand": _identity("B", "LIG", 1),
        "residues": [_identity("A", "ALA", 1)],
    }]


def test_reads_multiple_structure_artifacts(gemmi, tmp_path):
    paths = [
        _write_structure(gemmi, tmp_path / name, [_protein() + [_ligand()]])
        for name in ("first.pdb", "second.cif")
    ]
    method = PredictComplex()
    outputs = method.format_outputs({"predicted_structures": paths})

    records = method.extract_binding_sites(outputs)

    assert [record["structure"] for record in records] == [str(path) for path in paths]
    assert all(record["residues"] == [_identity("A", "ALA", 1)] for record in records)


def test_atom_records_can_represent_nonpolymer_ligands(gemmi, tmp_path):
    path = _write_structure(gemmi, tmp_path / "atom-ligand.pdb", [
        _protein() + [_ligand(het="A")]
    ])
    assert any(line.startswith("ATOM") and " LIG " in line for line in path.read_text().splitlines())

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert records[0]["ligand"] == _identity("B", "LIG", 1)
    assert records[0]["residues"] == [_identity("A", "ALA", 1)]


def test_strict_cutoff_deduplicates_residues_in_structure_order(gemmi, tmp_path):
    path = _write_structure(gemmi, tmp_path / "cutoff.pdb", [[
        _residue("A", "GLY", 9, [("CA", "C", 5, 0, 0)]),
        _residue("A", "ALA", 2, [("CA", "C", 4.999, 0, 0), ("CB", "C", 4.999, 0, 0)]),
        _residue("A", "TYR", 42, [("CA", "C", 3.5, 0, 0)]),
        _ligand(x=-1),
    ]])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert records[0]["residues"] == [_identity("A", "ALA", 2), _identity("A", "TYR", 42)]


def test_cutoff_uses_cartesian_distance_in_three_dimensions(gemmi, tmp_path):
    path = _write_structure(gemmi, tmp_path / "cartesian.pdb", [[
        _residue("A", "ALA", 1, [("CA", "C", 2, 3, 3)]),
        _residue("A", "GLY", 2, [("CA", "C", 3, 4, 0)]),
        _residue("A", "TYR", 3, [("CA", "C", 0, 0, 5)]),
        _ligand(x=-1),
    ]])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert records[0]["residues"] == [_identity("A", "ALA", 1)]


@pytest.mark.parametrize("element", ["H", "D"])
def test_hydrogen_and_deuterium_contacts_do_not_count(gemmi, tmp_path, element):
    path = _write_structure(gemmi, tmp_path / "hydrogens.pdb", [[
        _residue("A", "ALA", 1, [("CA", "C", 20, 0, 0), ("H", element, 1, 0, 0)]),
        _residue("A", "GLY", 2, [("CA", "C", 100, 0, 0)]),
        _residue("B", "LIG", 1, [
            ("C1", "C", 0, 0, 0), ("O1", "O", -1, 0, 0), ("H1", element, 19, 0, 0)
        ], het="H"),
    ]])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert len(records) == 1
    assert records[0]["residues"] == []


def test_models_ligands_and_protein_chains_are_independent(gemmi, tmp_path):
    first_model = _protein() + _protein(x=100, chain="C") + [
        _ligand(x=1), _ligand(x=101, chain="D", number=7, name="DRG")
    ]
    second_model = _protein(x=200) + [_ligand(x=1)]
    path = _write_structure(gemmi, tmp_path / "ensemble.pdb", [first_model, second_model])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert [(record["model"], record["ligand"]["chain_id"]) for record in records] == [
        ("1", "B"), ("1", "D"), ("2", "B")
    ]
    assert [record["residues"] for record in records] == [
        [_identity("A", "ALA", 1)], [_identity("C", "ALA", 1)], []
    ]


def test_water_ions_buffers_and_nucleic_acids_are_excluded(gemmi, tmp_path):
    path = _write_structure(gemmi, tmp_path / "excluded.pdb", [[
        *_protein(), _ligand(),
        _residue("W", "HOH", 1, [("O", "O", 1, 0, 0)], het="H"),
        _residue("I", "NA", 1, [("NA", "Na", 1, 0, 0)], het="H"),
        _residue("J", "CL", 1, [("CL", "Cl", 1, 0, 0)], het="H"),
        _residue("S", "SO4", 1, [("S", "S", 1, 0, 0), ("O1", "O", 2, 0, 0)], het="H"),
        _residue("D", "DA", 1, [("P", "P", 1, 0, 0), ("C1'", "C", 2, 0, 0)]),
        _residue("D", "DT", 2, [("P", "P", 1, 0, 0), ("C1'", "C", 2, 0, 0)]),
        _residue("R", "A", 1, [("P", "P", 1, 0, 0), ("C1'", "C", 2, 0, 0)]),
        _residue("R", "U", 2, [("P", "P", 1, 0, 0), ("C1'", "C", 2, 0, 0)]),
    ]])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert len(records) == 1
    assert records[0]["ligand"] == _identity("B", "LIG", 1)
    assert records[0]["residues"] == [_identity("A", "ALA", 1)]


@pytest.mark.parametrize("name", ["HEM", "ATP", "NAD"])
def test_cofactors_are_treated_as_ligands(gemmi, tmp_path, name):
    path = _write_structure(gemmi, tmp_path / "cofactor.pdb", [
        _protein() + [_ligand(name=name)]
    ])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert records[0]["ligand"] == _identity("B", name, 1)
    assert records[0]["residues"] == [_identity("A", "ALA", 1)]


def test_modified_amino_acids_and_insertion_codes(gemmi, tmp_path):
    path = _write_structure(gemmi, tmp_path / "modified.pdb", [[
        _residue("A", "ALA", 41, [("CA", "C", 50, 0, 0)]),
        _residue("A", "MSE", 42, [("CA", "C", 0, 0, 0)], insertion="B", het="H"),
        _ligand(),
    ]])

    records = PredictComplex().extract_binding_sites({"predicted_structures": path})

    assert records[0]["residues"] == [_identity("A", "MSE", 42, "B")]


@pytest.mark.parametrize("missing", ["protein", "ligand"])
def test_requires_protein_and_ligand_in_each_model(gemmi, tmp_path, missing):
    valid = _protein() + [_ligand()]
    incomplete = [_ligand()] if missing == "protein" else _protein()
    path = _write_structure(gemmi, tmp_path / "incomplete.pdb", [valid, incomplete])

    with pytest.raises(RuntimeError) as exc:
        PredictComplex().extract_binding_sites({"predicted_structures": path})

    message = str(exc.value).lower()
    assert str(path).lower() in message
    assert "2" in message and missing in message


@pytest.mark.parametrize("outputs", [{}, {"predicted_structures": None}, {"predicted_structures": []}])
def test_missing_structure_values_are_errors(outputs):
    with pytest.raises(RuntimeError, match="predicted_structures"):
        PredictComplex().extract_binding_sites(outputs)


def test_missing_structure_file_is_an_error(gemmi, tmp_path):
    path = tmp_path / "missing.pdb"
    with pytest.raises(RuntimeError) as exc:
        PredictComplex().extract_binding_sites({"predicted_structures": path})
    assert str(path) in str(exc.value)


@pytest.mark.parametrize("text", ["this is not a PDB structure\n", "", "ATOM      1  CA  ALA A   1       invalid\n"])
def test_malformed_structure_is_an_error(gemmi, tmp_path, text):
    path = tmp_path / "malformed.pdb"
    path.write_text(text)
    with pytest.raises(RuntimeError) as exc:
        PredictComplex().extract_binding_sites({"predicted_structures": path})
    assert str(path) in str(exc.value)


def test_inline_structure_artifacts_are_rejected(gemmi):
    artifact = ComplexStructure.inline({"atoms": []}, format="pdb")
    with pytest.raises(RuntimeError, match="(?i)(file|inline)"):
        PredictComplex().extract_binding_sites({"predicted_structures": [artifact]})


@pytest.mark.parametrize("as_artifact", [False, True])
def test_unsupported_structure_format_is_rejected(gemmi, tmp_path, as_artifact):
    path = tmp_path / "complex.pdbqt"
    path.write_text("unsupported docking structure\n")
    value = ComplexStructure.from_file(path, format="pdbqt") if as_artifact else path
    with pytest.raises(RuntimeError, match="(?i)(pdbqt|unsupported|format)"):
        PredictComplex().extract_binding_sites({"predicted_structures": value})


def test_missing_optional_gemmi_has_a_clear_error(monkeypatch, tmp_path):
    path = tmp_path / "complex.pdb"
    path.write_text("HEADER    requires optional Gemmi parser\n")
    original_import = builtins.__import__

    def import_without_gemmi(name, *args, **kwargs):
        if name == "gemmi":
            raise ImportError("Gemmi is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_gemmi)
    with pytest.raises(RuntimeError, match="(?i)gemmi"):
        PredictComplex().extract_binding_sites({"predicted_structures": path})
