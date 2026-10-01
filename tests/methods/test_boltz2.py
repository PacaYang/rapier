"""Hermetic tests for the Boltz-2 normalized method."""

import json
from pathlib import Path

from rapier.artifacts import Artifact, ArtifactStorage
from rapier.core.enums import TaskStatus
from rapier.core.task import Task
from rapier.methods.prediction.boltz2 import Boltz2Prediction
from rapier.methods.prediction import boltz2_runner


def _task(tmp_path):
    method = Boltz2Prediction(parameters={
        "use_msa_server": True,
        "affinity_binder": "B",
        "diffusion_samples": 2,
        "accelerator": "cpu",
    })
    task = Task(
        task_id="boltz2_test",
        method=method,
        inputs={
            "proteins": [Artifact(
                "sequence.protein",
                "inline",
                "MKTAYIAKQRQISFVKSHFSRQ",
                storage=ArtifactStorage.INLINE,
                metadata={"chain_id": "A"},
            )],
            "ligands": [Artifact(
                "molecule",
                "smiles",
                "CCO",
                storage=ArtifactStorage.INLINE,
                metadata={"chain_id": "B"},
            )],
        },
    )
    task.work_dir = tmp_path / "work"
    return task


def test_boltz2_contract_and_manifest(tmp_path):
    task = _task(tmp_path)
    method = task.method
    command = method.get_command(task)
    document = __import__("yaml").safe_load((task.work_dir / "boltz2_input.yaml").read_text())

    assert method.register_name == "boltz2"
    assert "boltz2_runner" in command
    assert document["version"] == 1
    assert document["sequences"][0]["protein"]["id"] == "A"
    assert document["sequences"][1]["ligand"] == {"id": "B", "smiles": "CCO"}
    assert document["properties"] == [{"affinity": {"binder": "B"}}]


def _fake_boltz(command, cwd):
    out_dir = Path(command[command.index("--out_dir") + 1])
    sample_dir = out_dir / "predictions" / "boltz2_input"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "boltz2_input_model_0.cif").write_text("data_model\n")
    (sample_dir / "boltz2_input_model_1.cif").write_text("data_model_1\n")
    (sample_dir / "confidence_boltz2_input.json").write_text(
        json.dumps({"confidence_score": 0.9})
    )
    (sample_dir / "affinity_boltz2_input.json").write_text(
        json.dumps({"affinity_pred_value": -3.2, "affinity_probability_binary": 0.8})
    )
    (sample_dir / "plddt_boltz2_input_model_0.npz").write_bytes(b"NPZ")
    return 0, "Boltz complete", ""


def test_boltz2_runner_collects_structure_confidence_and_affinity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)

    assert task.method.execute(task) == TaskStatus.COMPLETED
    assert len(task.outputs["predicted_structures"]) == 2
    assert task.outputs["predicted_structures"][0].type == "structure.complex"
    assert len(task.outputs["confidence_scores"]) == 1
    assert len(task.outputs["affinity_scores"]) == 1
    assert task.outputs["prediction_arrays"][0].format == "npz"
    assert task.outputs["run_summary"].format == "json"


def test_boltz2_requires_msa_without_server_or_single_sequence_mode(tmp_path):
    method = Boltz2Prediction(parameters={
        "use_msa_server": False,
        "single_sequence_mode": False,
    })
    task = _task(tmp_path)
    task.method = method
    valid, errors = method.validate_inputs(task.inputs)
    assert valid, errors
    try:
        method.prepare(task)
    except ValueError as exc:
        assert "no MSA" in str(exc)
    else:
        raise AssertionError("Expected missing-MSA validation failure")


def test_boltz2_accepts_normalized_constraints_json(tmp_path):
    task = _task(tmp_path)
    constraints = tmp_path / "constraints.json"
    constraints.write_text(json.dumps({
        "constraints": [{
            "pocket": {
                "binder": "B",
                "contacts": [["A", 1]],
                "max_distance": 6,
            }
        }]
    }))
    task.inputs["constraints"] = Artifact("file", "json", constraints)
    task.method.prepare(task)
    document = __import__("yaml").safe_load((task.work_dir / "boltz2_input.yaml").read_text())
    assert document["constraints"][0]["pocket"]["binder"] == "B"


def test_boltz2_supports_ccd_ligand_artifacts(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["ligands"] = [Artifact(
        "molecule", "ccd", "ATP", storage=ArtifactStorage.INLINE,
        metadata={"chain_id": "B"},
    )]
    method.prepare(task)
    document = __import__("yaml").safe_load((task.work_dir / "boltz2_input.yaml").read_text())
    assert document["sequences"][1]["ligand"] == {"id": "B", "ccd": "ATP"}


def test_boltz2_supports_identical_polymer_chain_ids(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["proteins"] = [Artifact(
        "sequence.protein", "inline", "MKT", storage=ArtifactStorage.INLINE,
        metadata={"chain_ids": ["A", "C"]},
    )]
    method.prepare(task)
    document = __import__("yaml").safe_load((task.work_dir / "boltz2_input.yaml").read_text())
    assert document["sequences"][0]["protein"]["id"] == ["A", "C"]


def _write_test_complex(path, ligand_offset=0.0):
    gemmi = __import__("gemmi")
    structure = gemmi.Structure()
    model = gemmi.Model("1")

    protein = gemmi.Chain("A")
    residue = gemmi.Residue()
    residue.name = "ALA"
    residue.seqid = gemmi.SeqId(1, " ")
    for name, xyz in (("N", (0.0, 0.0, 0.0)), ("CA", (1.5, 0.0, 0.0)), ("C", (2.0, 1.0, 0.0))):
        atom = gemmi.Atom()
        atom.name = name
        atom.element = gemmi.Element(name[0])
        atom.pos = gemmi.Position(*xyz)
        residue.add_atom(atom)
    protein.add_residue(residue)
    model.add_chain(protein)

    for chain_id, residue_name, xyz in (
        ("B", "LIG", (10.0 + ligand_offset, 20.0, 30.0)),
        ("C", "LIG", (14.0 + ligand_offset, 24.0, 34.0)),
    ):
        chain = gemmi.Chain(chain_id)
        ligand = gemmi.Residue()
        ligand.name = residue_name
        ligand.seqid = gemmi.SeqId(1, " ")
        atom = gemmi.Atom()
        atom.name = "C1"
        atom.element = gemmi.Element("C")
        atom.pos = gemmi.Position(*xyz)
        ligand.add_atom(atom)
        chain.add_residue(ligand)
        model.add_chain(chain)
    structure.add_model(model)
    structure.write_pdb(str(path))


def test_boltz2_emits_receptor_and_combined_docking_box(tmp_path, monkeypatch):
    __import__("pytest").importorskip("gemmi")

    def fake_boltz(command, cwd):
        out_dir = Path(command[command.index("--out_dir") + 1])
        sample_dir = out_dir / "predictions" / "boltz2_input"
        sample_dir.mkdir(parents=True, exist_ok=True)
        _write_test_complex(sample_dir / "boltz2_input_model_0.pdb", ligand_offset=0.0)
        _write_test_complex(sample_dir / "boltz2_input_model_1.pdb", ligand_offset=2.0)
        (sample_dir / "confidence_boltz2_input_model_0.json").write_text(
            json.dumps({"confidence_score": 0.2})
        )
        (sample_dir / "confidence_boltz2_input_model_1.json").write_text(
            json.dumps({"confidence_score": 0.9})
        )
        return 0, "Boltz complete", ""

    monkeypatch.setattr(boltz2_runner, "_run_boltz", fake_boltz)
    method = Boltz2Prediction(parameters={
        "accelerator": "cpu",
        "emit_docking_inputs": True,
        "docking_box_padding": 8.0,
    })
    task = Task(
        task_id="boltz2_docking_handoff",
        method=method,
        inputs={
            "proteins": [Artifact(
                "sequence.protein", "inline", "MKT", storage=ArtifactStorage.INLINE,
                metadata={"chain_id": "A"},
            )],
            "ligands": [Artifact(
                "molecule.collection", "smiles", ["CCO", "CCC"],
                storage=ArtifactStorage.INLINE,
                metadata={"chain_id": ["B", "C"]},
            )],
        },
    )
    task.work_dir = tmp_path / "work"

    assert method.execute(task) == TaskStatus.COMPLETED
    receptor = task.outputs["vina_receptor"]
    box = task.outputs["docking_box"]
    assert receptor.type == "structure.protein"
    assert receptor.format == "pdb"
    assert receptor.path.exists()
    assert box.type == "binding_site"
    assert box.value["center"] == [14.0, 22.0, 32.0]
    assert box.value["size"] == [20.0, 20.0, 20.0]
    assert task.outputs["run_summary"].path.exists()
    summary = json.loads(task.outputs["run_summary"].path.read_text())
    assert summary["selected_confidence"] == 0.9
    assert summary["selection_fallback"] is False
    receptor_structure = __import__("gemmi").read_structure(str(receptor.path))
    assert [chain.name for chain in receptor_structure[0]] == ["A"]
