"""Hermetic tests for the Boltz-2 normalized method."""

import json
import struct
from pathlib import Path
import zipfile

import pytest

from rapier.artifacts import Artifact, ArtifactStorage
from rapier.core.enums import TaskStatus
from rapier.core.task import Task
from rapier.core.method import RunContext
from rapier.data import BindingSite, ComplexStructure, ProteinStructure, Report, Scores
from rapier.methods.prediction.boltz2 import Boltz2Prediction
from rapier.methods.prediction import boltz2_runner
from rapier.methods.runner import read_outputs, run_task_file


@pytest.fixture(autouse=True)
def available_mock_tools(monkeypatch):
    monkeypatch.setattr(
        Boltz2Prediction, "validate_environment", lambda self: (True, [])
    )


def _task(tmp_path):
    method = Boltz2Prediction(
        parameters={
            "use_msa_server": True,
            "affinity_binder": "B",
            "diffusion_samples": 2,
            "accelerator": "cpu",
        }
    )
    task = Task(
        task_id="boltz2_test",
        method=method,
        inputs={
            "proteins": [
                Artifact(
                    "sequence.protein",
                    "inline",
                    "MKTAYIAKQRQISFVKSHFSRQ",
                    storage=ArtifactStorage.INLINE,
                    metadata={"chain_id": "A"},
                )
            ],
            "ligands": [
                Artifact(
                    "molecule",
                    "smiles",
                    "CCO",
                    storage=ArtifactStorage.INLINE,
                    metadata={"chain_id": "B"},
                )
            ],
        },
    )
    task.work_dir = tmp_path / "work"
    return task


def test_boltz2_contract_and_manifest(tmp_path):
    task = _task(tmp_path)
    method = task.method
    command = method.get_command(task)
    task_file = json.loads((task.work_dir / "task.json").read_text())
    assert task_file["method"]["config"]["diffusion_samples"] == 2
    assert "-m rapier.methods.runner --task" in command
    assert not (task.work_dir / "boltz2_input.yaml").exists()
    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )

    assert method.register_name == "boltz2"
    assert document["version"] == 1
    assert document["sequences"][0]["protein"]["id"] == "A"
    assert document["sequences"][1]["ligand"] == {"id": "B", "smiles": "CCO"}
    assert document["properties"] == [{"affinity": {"binder": "B"}}]


def _fake_boltz(command, cwd):
    out_dir = Path(command[command.index("--out_dir") + 1])
    sample_dir = out_dir / "predictions" / "boltz2_input"
    sample_dir.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        (sample_dir / f"boltz2_input_model_{index}.cif").write_text(
            "data_model\nloop_\n_atom_site.group_PDB\n_atom_site.id\n"
            "_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n"
            "ATOM 1 0.0 1.0 2.0\n#\n"
        )
    (sample_dir / "confidence_boltz2_input.json").write_text(
        json.dumps({"confidence_score": 0.9})
    )
    (sample_dir / "affinity_boltz2_input.json").write_text(
        json.dumps({"affinity_pred_value": -3.2, "affinity_probability_binary": 0.8})
    )
    with zipfile.ZipFile(sample_dir / "plddt_boltz2_input_model_0.npz", "w") as handle:
        header = b"{'descr': '<f8', 'fortran_order': False, 'shape': (1,), }"
        header += b" " * ((16 - (10 + len(header) + 1) % 16) % 16) + b"\n"
        handle.writestr(
            "plddt.npy",
            b"\x93NUMPY\x01\x00"
            + struct.pack("<H", len(header))
            + header
            + struct.pack("<d", 0.9),
        )
    return 0, "Boltz complete", ""


def test_boltz2_runner_collects_structure_confidence_and_affinity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert len(task.outputs["predicted_structures"]) == 2
    assert task.outputs["predicted_structures"][0].type == "structure.complex"
    assert len(task.outputs["confidence_scores"]) == 1
    assert len(task.outputs["affinity_scores"]) == 1
    assert task.outputs["prediction_arrays"][0].format == "npz"
    assert task.outputs["run_summary"].format == "json"


def test_boltz2_generic_runner_validates_once_and_collects_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)
    calls = []
    validate = Boltz2Prediction.validate_outputs

    def record_validation(self, inputs, outputs, ctx):
        calls.append(set(outputs))
        return validate(self, inputs, outputs, ctx)

    monkeypatch.setattr(Boltz2Prediction, "validate_outputs", record_validation)
    task.method.get_command(task)
    result = run_task_file(task.work_dir / "task.json")

    assert result["status"] == "completed", result["error"]
    assert result["validation_version"] == 1
    assert len(calls) == 1
    assert len(read_outputs(task)["predicted_structures"]) == 2


def test_boltz2_missing_msa_stops_before_prediction(tmp_path, monkeypatch):
    task = _task(tmp_path)
    task.method = Boltz2Prediction(config={"use_msa_server": False})
    calls = []
    monkeypatch.setattr(boltz2_runner, "_run_boltz", lambda *args: calls.append(args))

    assert task.method.execute(task) == TaskStatus.FAILED
    assert "no MSA" in task.error_message
    assert not calls
    assert not task.outputs


@pytest.mark.parametrize("binder", ["missing", "A", None])
def test_boltz2_affinity_property_requires_actual_ligand_binder(tmp_path, binder):
    task = _task(tmp_path)
    task.method = Boltz2Prediction(
        config={"properties": [{"affinity": {"binder": binder}}]}
    )
    ctx = RunContext.for_task(task)
    task.inputs = task.method.format_inputs(task.inputs, ctx.input_dir)

    errors = task.method.check_dependencies(task.inputs, ctx)

    assert any("binder" in message.lower() for message in errors)


def test_boltz2_requires_all_configured_models(tmp_path, monkeypatch):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)
    task.method.config["diffusion_samples"] = 3

    assert task.method.execute(task) == TaskStatus.FAILED
    assert "expected 3 from diffusion_samples" in task.error_message
    assert not task.outputs


def test_boltz2_pooled_validation_reports_independent_bad_outputs(tmp_path):
    task = _task(tmp_path)
    method = Boltz2Prediction(
        config={
            "emit_docking_inputs": True,
            "properties": [{"affinity": {"binder": "B"}}],
        }
    )
    task.method = method
    ctx = RunContext.for_task(task)
    model = tmp_path / "sample_model_0.cif"
    model.write_text("data_empty\n")
    confidence = tmp_path / "confidence_sample.json"
    confidence.write_text("null")
    affinity = tmp_path / "affinity_sample.json"
    affinity.write_text("[]")
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("BROKEN\n")
    outputs = {
        "predicted_structures": [ComplexStructure.from_file(model)],
        "confidence_scores": [Report.from_file(confidence)],
        "affinity_scores": [Report.from_file(affinity)],
        "scores": Scores.inline([None, {"id": "unexpected"}], "json"),
        "run_summary": Report.inline(
            {"status": "failed", "structures": [str(model)]}, "json"
        ),
        "vina_receptor": ProteinStructure.from_file(receptor),
        "docking_box": BindingSite.inline(
            {"center": [0, "nan", 0], "size": [1, -1, "bad"]}, "box"
        ),
    }

    errors = method.validate_outputs(task.inputs, outputs, ctx)
    messages = "; ".join(errors)

    assert "no readable atom coordinates" in messages
    assert "must contain a JSON object" in messages
    assert "status is not completed" in messages
    assert "unknown model" in messages
    assert "invalid confidence_score" in messages
    assert "row 0" in messages
    assert "missing model IDs" in messages
    assert "docking_box size" in messages
    assert "docking_box center" in messages
    assert len(errors) >= 8


def test_boltz2_collects_independent_files_when_summary_is_malformed(tmp_path):
    task = _task(tmp_path)
    ctx = RunContext.for_task(task)
    prediction_dir = ctx.work_dir / "boltz2_output" / "predictions" / "sample"
    prediction_dir.mkdir(parents=True)
    model = prediction_dir / "sample_model_0.cif"
    model.write_text("data_model\n")
    (prediction_dir / "confidence_sample.json").write_text('{"confidence_score": 0.8}')
    (prediction_dir / "affinity_sample.json").write_text("not json")
    (ctx.output_dir / "boltz2_run.json").write_text("{broken")

    outputs = task.method._collect(ctx.work_dir, ctx.output_errors)

    assert outputs["predicted_structures"][0].path == model
    assert len(outputs["confidence_scores"]) == 1
    assert len(outputs["affinity_scores"]) == 1
    assert outputs["scores"].rows()[0]["id"] == model.stem
    assert any("run summary" in message for message in ctx.output_errors)
    assert any("affinity report" in message for message in ctx.output_errors)


def test_boltz2_confidence_rows_must_match_report(tmp_path, monkeypatch):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    outputs = dict(task.outputs)
    rows = outputs["scores"].rows()
    rows[0]["confidence_score"] = 0.1
    outputs["scores"] = Scores.inline(rows, "json")

    errors = task.method.validate_outputs(
        task.inputs, outputs, RunContext.for_task(task)
    )

    assert any("confidence_score disagrees" in message for message in errors)


def test_boltz2_requires_msa_without_server_or_single_sequence_mode(tmp_path):
    method = Boltz2Prediction(
        parameters={
            "use_msa_server": False,
            "single_sequence_mode": False,
        }
    )
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
    constraints.write_text(
        json.dumps(
            {
                "constraints": [
                    {
                        "pocket": {
                            "binder": "B",
                            "contacts": [["A", 1]],
                            "max_distance": 6,
                        }
                    }
                ]
            }
        )
    )
    task.inputs["constraints"] = Artifact("file", "json", constraints)
    task.method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    assert document["constraints"][0]["pocket"]["binder"] == "B"


def test_boltz2_supports_ccd_ligand_artifacts(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["ligands"] = [
        Artifact(
            "molecule",
            "ccd",
            "ATP",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "B"},
        )
    ]
    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    assert document["sequences"][1]["ligand"] == {"id": "B", "ccd": "ATP"}


def test_boltz2_supports_identical_polymer_chain_ids(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["proteins"] = [
        Artifact(
            "sequence.protein",
            "inline",
            "MKT",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_ids": ["A", "C"]},
        )
    ]
    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    assert [
        item["protein"]["id"] for item in document["sequences"] if "protein" in item
    ] == ["A", "C"]


def _msa_artifact(chain_id, sequence, fmt="a3m"):
    payload = f">query\n{sequence}\n>hit\n{sequence}\n"
    return Artifact(
        "file",
        fmt,
        payload,
        storage=ArtifactStorage.INLINE,
        metadata={"chain_id": chain_id},
    )


def test_boltz2_unified_msa_covers_multiple_protein_chains(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["proteins"] = [
        Artifact(
            "sequence.protein",
            "inline",
            "MKT",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "A"},
        ),
        Artifact(
            "sequence.protein",
            "inline",
            "GGA",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "B"},
        ),
    ]
    task.inputs["msa"] = [_msa_artifact("A", "MKT"), _msa_artifact("B", "GGA")]

    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    proteins = [item["protein"] for item in document["sequences"] if "protein" in item]

    assert [(item["id"], item["sequence"]) for item in proteins] == [
        ("A", "MKT"),
        ("B", "GGA"),
    ]
    assert all(Path(item["msa"]).read_text().startswith(">query") for item in proteins)
    manifest = json.loads((task.work_dir / "boltz2_manifest.json").read_text())
    assert manifest["msa_supplied"] is True


def test_boltz2_unified_msa_requires_exact_chain_coverage(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["proteins"] = [
        Artifact(
            "sequence.protein",
            "inline",
            "MKT",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "A"},
        ),
        Artifact(
            "sequence.protein",
            "inline",
            "GGA",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "B"},
        ),
    ]
    task.inputs["msa"] = [_msa_artifact("A", "MKT")]

    with pytest.raises(ValueError, match="coverage|chain.*B"):
        method.prepare(task)


def test_boltz2_unified_msa_overrides_metadata_msa(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    old = tmp_path / "old.a3m"
    old.write_text(">query\nMKT\n")
    task.inputs["proteins"] = [
        Artifact(
            "sequence.protein",
            "inline",
            "MKT",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "A", "msa": str(old)},
        )
    ]
    task.inputs["msa"] = [_msa_artifact("A", "MKT")]

    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    assert (
        Path(document["sequences"][0]["protein"]["msa"]).read_text() != old.read_text()
    )


def test_boltz2_unified_msa_disables_templates(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["msa"] = [_msa_artifact("A", "MKTAYIAKQRQISFVKSHFSRQ")]
    # This is deliberately inline and malformed as a Boltz template.  A
    # supplied MSA must take precedence without trying to inspect it.
    task.inputs["templates"] = [
        Artifact(
            "structure",
            "pdb",
            "missing template",
            storage=ArtifactStorage.INLINE,
            metadata={"chain_id": "A"},
        )
    ]

    method.prepare(task)
    document = __import__("yaml").safe_load(
        (task.work_dir / "boltz2_input.yaml").read_text()
    )
    assert "templates" not in document


def test_boltz2_unified_msa_drops_missing_template_before_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(boltz2_runner, "_run_boltz", _fake_boltz)
    task = _task(tmp_path)
    task.inputs["msa"] = [_msa_artifact("A", "MKTAYIAKQRQISFVKSHFSRQ")]
    task.inputs["templates"] = [
        Artifact.from_file(
            tmp_path / "missing-template.pdb",
            "pdb",
            metadata={"chain_id": "A"},
            type_name="structure",
        )
    ]

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message


def test_boltz2_direct_validation_drops_missing_template_with_msa(tmp_path):
    method = Boltz2Prediction()
    task = _task(tmp_path)
    task.inputs["msa"] = [_msa_artifact("A", "MKTAYIAKQRQISFVKSHFSRQ")]
    task.inputs["templates"] = [
        Artifact.from_file(
            tmp_path / "missing-template.pdb",
            "pdb",
            metadata={"chain_id": "A"},
            type_name="structure",
        )
    ]

    valid, errors = method.validate_inputs(task.inputs)
    assert valid, errors


def test_boltz2_run_manifest_disables_server_for_supplied_msa(tmp_path, monkeypatch):
    input_yaml = tmp_path / "input.yaml"
    input_yaml.write_text(
        "version: 1\nsequences:\n"
        "- protein:\n    id: A\n    sequence: MKT\n    msa: /tmp/A.a3m\n"
        "templates:\n- pdb: /tmp/stale-template.pdb\n"
    )
    output_dir = tmp_path / "output"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "work_dir": str(tmp_path),
                "input_yaml": str(input_yaml),
                "output_dir": str(output_dir),
                "sample_name": "input",
                "msa_supplied": True,
                "config": {
                    "use_msa_server": True,
                    "msa_server_url": "https://example.invalid",
                    "msa_pairing_strategy": "greedy",
                },
            }
        )
    )
    seen = {}

    def fake_run(command, cwd):
        seen["command"] = command
        seen["document"] = __import__("yaml").safe_load(input_yaml.read_text())
        prediction_dir = output_dir / "predictions" / "input"
        prediction_dir.mkdir(parents=True)
        (prediction_dir / "input_model_0.cif").write_text("data_model\n")
        return 0, "ok", ""

    monkeypatch.setattr(boltz2_runner, "_run_boltz", fake_run)
    boltz2_runner.run_manifest(manifest)
    assert "--use_msa_server" not in seen["command"]
    assert "--msa_server_url" not in seen["command"]
    assert "templates" not in seen["document"]


def test_boltz2_run_manifest_keeps_server_without_unified_msa(tmp_path, monkeypatch):
    input_yaml = tmp_path / "input.yaml"
    input_yaml.write_text(
        "version: 1\nsequences:\n" "- protein:\n    id: A\n    sequence: MKT\n"
    )
    output_dir = tmp_path / "output"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "work_dir": str(tmp_path),
                "input_yaml": str(input_yaml),
                "output_dir": str(output_dir),
                "sample_name": "input",
                "msa_supplied": False,
                "config": {
                    "use_msa_server": True,
                    "msa_server_url": "https://example.invalid",
                    "msa_pairing_strategy": "greedy",
                },
            }
        )
    )
    seen = {}

    def fake_run(command, cwd):
        seen["command"] = command
        prediction_dir = output_dir / "predictions" / "input"
        prediction_dir.mkdir(parents=True)
        (prediction_dir / "input_model_0.cif").write_text("data_model\n")
        return 0, "ok", ""

    monkeypatch.setattr(boltz2_runner, "_run_boltz", fake_run)
    boltz2_runner.run_manifest(manifest)
    assert "--use_msa_server" in seen["command"]
    assert "--msa_server_url" in seen["command"]


def _write_test_complex(path, ligand_offset=0.0):
    gemmi = __import__("gemmi")
    structure = gemmi.Structure()
    model = gemmi.Model("1")

    protein = gemmi.Chain("A")
    residue = gemmi.Residue()
    residue.name = "ALA"
    residue.seqid = gemmi.SeqId(1, " ")
    for name, xyz in (
        ("N", (0.0, 0.0, 0.0)),
        ("CA", (1.5, 0.0, 0.0)),
        ("C", (2.0, 1.0, 0.0)),
    ):
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
    method = Boltz2Prediction(
        parameters={
            "accelerator": "cpu",
            "diffusion_samples": 2,
            "output_format": "pdb",
            "emit_docking_inputs": True,
            "docking_box_padding": 8.0,
        }
    )
    task = Task(
        task_id="boltz2_docking_handoff",
        method=method,
        inputs={
            "proteins": [
                Artifact(
                    "sequence.protein",
                    "inline",
                    "MKT",
                    storage=ArtifactStorage.INLINE,
                    metadata={"chain_id": "A"},
                )
            ],
            "ligands": [
                Artifact(
                    "molecule.collection",
                    "smiles",
                    ["CCO", "CCC"],
                    storage=ArtifactStorage.INLINE,
                    metadata={"chain_id": ["B", "C"]},
                )
            ],
        },
    )
    task.work_dir = tmp_path / "work"

    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
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
