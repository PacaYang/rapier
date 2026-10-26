"""Predictor contracts and lifecycles without installed scientific software."""

import csv
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from rapier.core.enums import TaskStatus
from rapier.core.method import RunContext
from rapier.core.task import Task
from rapier.data import File, Molecule, Molecules
from rapier.methods import AF3Prediction, DrugLAMP, NessoPrediction, get_registry
from rapier.methods.prediction._external import ExternalPrediction, ligand_inventory
from rapier.methods.prediction.nesso import _validate_tensors
from rapier.methods.prediction._worker import af3_options
from rapier.methods.runner import read_outputs, run_task_file

METHODS = [AF3Prediction, DrugLAMP, NessoPrediction]


def _native_document(**updates):
    document = {
        "dialect": "alphafold3",
        "version": 4,
        "name": "prepared",
        "modelSeeds": [2],
        "sequences": [
            {
                "protein": {
                    "id": "A",
                    "sequence": "ACDE",
                    "unpairedMsa": "",
                    "pairedMsa": "",
                    "templates": [],
                }
            },
            {"ligand": {"id": "B", "smiles": "CCO"}},
        ],
    }
    document.update(updates)
    return document


def _write_json(path, data):
    path.write_text(json.dumps(data))


def _cif(offset):
    return (
        "data_model\nloop_\n_atom_site.label_asym_id\n_atom_site.label_atom_id\n"
        "_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n"
        f"A CA 0 0 0\nB C1 {offset} 1 2\n#\n"
    )


def _af3_outputs(directory, samples):
    document = json.loads((directory / "input.json").read_text())
    chains = [
        chain
        for entity in document["sequences"]
        for payload in entity.values()
        for chain in (
            payload["id"] if isinstance(payload["id"], list) else [payload["id"]]
        )
    ]
    count = len(chains)
    root = directory / "native" / "job"
    root.mkdir(parents=True)
    ranking = []
    for seed in document["modelSeeds"]:
        for sample in range(samples):
            score = 0.8 + sample * 0.01
            sub = root / f"seed-{seed}_sample-{sample}"
            sub.mkdir()
            prefix = f"job_seed-{seed}_sample-{sample}"
            cif = _cif(sample + 1)
            if chains != ["A", "B"]:
                cif = cif[: cif.index("A CA")]
                cif += (
                    "".join(
                        f"{chain} CA {sample + index} 1 2\n"
                        for index, chain in enumerate(chains)
                    )
                    + "#\n"
                )
            (sub / f"{prefix}_model.cif").write_text(cif)
            summary = {
                "ranking_score": score,
                "ptm": 0.7,
                "iptm": 0.8,
                "fraction_disordered": 0.1,
                "has_clash": 0,
                "chain_ptm": [0.7] * count,
                "chain_iptm": [0.8] * count,
                "chain_pair_pae_min": [[0] * count for _ in chains],
                "chain_pair_iptm": [[0.8] * count for _ in chains],
            }
            full = {
                "atom_chain_ids": chains,
                "atom_plddts": [80] * count,
                "token_chain_ids": chains,
                "token_res_ids": [1] * count,
                "pae": [[0] * count for _ in chains],
                "contact_probs": [[1] * count for _ in chains],
            }
            _write_json(sub / f"{prefix}_summary_confidences.json", summary)
            _write_json(sub / f"{prefix}_confidences.json", full)
            ranking.append({"seed": seed, "sample": sample, "ranking_score": score})
    with (root / "job_ranking_scores.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["seed", "sample", "ranking_score"])
        writer.writeheader()
        writer.writerows(ranking)
    seed, sample = document["modelSeeds"][0], samples - 1
    prefix = f"job_seed-{seed}_sample-{sample}"
    for suffix in ("_model.cif", "_summary_confidences.json", "_confidences.json"):
        shutil.copyfile(
            root / f"seed-{seed}_sample-{sample}" / (prefix + suffix),
            root / ("job" + suffix),
        )


def _tensor_file(path, omit=None):
    shapes = {
        "z": [2, 2, 3],
        "pdistogram": [2, 2, 4],
        "refine_mask": [3],
        "pocket_mask": [3],
        "token_pad_mask": [3],
        "mol_type": [2],
    }
    header, offset = {}, 0
    for name, shape in shapes.items():
        if name == omit:
            continue
        count = 1
        for dim in shape:
            count *= dim
        header[name] = {
            "dtype": "F32",
            "shape": shape,
            "data_offsets": [offset, offset + count * 4],
        }
        offset += count * 4
    encoded = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + b"\x00" * offset)


def _fake_worker(self, request, directory=None):
    if request["action"] == "ligands":
        cases = [dict(case) for case in request["cases"]]
        for case in cases:
            if case.get("smiles") == "invalid":
                case["error"] = "invalid ligand chemistry"
            else:
                case["prepared_smiles"] = case.get("smiles") or "CCO"
        return {"cases": cases}
    if request["action"] == "af3_input":
        document = (
            json.loads(Path(request["source"]).read_text())
            if request.get("source")
            else request["document"]
        )
        document = af3_options(document, request)
        if request.get("destination"):
            _write_json(Path(request["destination"]), document)
        return {"document": document}
    return {}


def _fake_command(self, arguments, directory, label):
    if label == "af3":
        _af3_outputs(directory, self.config["num_diffusion_samples"])
    elif label == "drug_lamp":
        with (directory / "input.csv").open() as handle:
            row = next(csv.DictReader(handle))
        row.update(Prediction=0.75, Predicted_Label=1)
        with (directory / "predictions.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    else:
        root = directory / "native" / "predictions" / "input"
        root.mkdir(parents=True)
        _write_json(
            root / "affinity.json",
            {
                "affinity_pred_value": -3.2,
                "affinity_probability_binary": 0.9,
                "entropy_pl": 0.2,
            },
        )
        if self.config["save_metadata"]:
            _tensor_file(root / "predictions.safetensors")


@pytest.fixture
def fake_apps(monkeypatch):
    monkeypatch.setattr(
        ExternalPrediction, "validate_environment", lambda self: (True, [])
    )

    def dependencies(self, inputs, ctx):
        self._runtime_assets = {
            "checkpoint": "model",
            "ccd": "ccd.pkl",
            "max_atoms": 512,
        }
        return []

    monkeypatch.setattr(ExternalPrediction, "check_dependencies", dependencies)
    monkeypatch.setattr(ExternalPrediction, "_worker", _fake_worker)
    monkeypatch.setattr(ExternalPrediction, "_command", _fake_command)


def _protein_port(method_class):
    return "proteins" if method_class is AF3Prediction else "protein"


def _task(tmp_path, method_class, config=None, ligands=None):
    method = method_class(config=config or {})
    task = Task(
        "predict",
        method,
        {
            _protein_port(method_class): "ACDE",
            "ligands": ligands or ["CCO ethanol", "CCN ethanol"],
        },
    )
    task.work_dir = tmp_path / "work"
    return task


@pytest.mark.parametrize("method_class", METHODS)
def test_registration_and_input_checks_without_scientific_packages(method_class):
    registry = get_registry()
    registry.auto_discover()
    assert registry.get(method_class.register_name) is method_class
    method = method_class()
    valid, errors = method.validate_inputs(
        {
            _protein_port(method_class): "ACDE",
            "ligands": Molecule.inline("CCO ethanol", "smiles"),
        }
    )
    assert valid, errors
    valid, errors = method.validate_inputs(
        {_protein_port(method_class): "AC-D*", "ligands": ["CCO"]}
    )
    assert not valid
    assert any("symbols" in error for error in errors)


@pytest.mark.parametrize("method_class", METHODS)
@pytest.mark.parametrize("content", [">a\nACDE\n>b\nFGHI\n", ">empty\n", "ACDE\n"])
def test_rejects_invalid_target_fasta(tmp_path, method_class, content):
    path = tmp_path / "target.fasta"
    path.write_text(content)
    valid, errors = method_class().validate_inputs(
        {_protein_port(method_class): path, "ligands": ["CCO"]}
    )
    assert not valid
    assert any("protein" in error for error in errors)


@pytest.mark.parametrize("method_class", METHODS)
def test_rejects_empty_libraries_and_unreadable_files(tmp_path, method_class):
    method = method_class()
    for ligands in ([], tmp_path / "missing.sdf"):
        assert not method.validate_inputs(
            {_protein_port(method_class): "ACDE", "ligands": ligands}
        )[0]


def test_drug_lamp_prevents_sequence_truncation_and_unsupported_j():
    for sequence in ("A" * 1023, "ACJDE"):
        assert not DrugLAMP().validate_inputs(
            {"protein": sequence, "ligands": ["CCO"]}
        )[0]
    assert DrugLAMP().validate_inputs({"protein": "A" * 1022, "ligands": ["CCO"]})[0]


def test_duplicate_ids_are_numbered_across_artifacts():
    cases, errors = ligand_inventory(
        {
            "ligands": [
                Molecules.inline("CCO same", "smiles"),
                Molecules.inline("CCN same", "smiles"),
            ]
        }
    )
    assert not errors
    assert [(case["id"], case["input_occurrence"]) for case in cases] == [
        ("same", 1),
        ("same", 2),
    ]


def test_af3_native_modes_and_relative_references(tmp_path):
    msa = tmp_path / "protein.a3m"
    msa.write_text(">protein\nACDE\n")
    document = _native_document()
    chain = document["sequences"][0]["protein"]
    del chain["unpairedMsa"]
    chain["unpairedMsaPath"] = "protein.a3m"
    path = tmp_path / "job.json"
    _write_json(path, document)
    method = AF3Prediction(config={"run_data_pipeline": False})
    prepared = method.format_inputs({"af3_input": path}, tmp_path / "staged")
    assert method.validate_inputs(prepared) == (True, [])
    valid, errors = method.validate_inputs(
        {"af3_input": path, "proteins": "ACDE", "ligands": ["CCO"]}
    )
    assert not valid and "mutually exclusive" in " ".join(errors)
    msa.unlink()
    assert not method.validate_inputs(prepared)[0]


@pytest.mark.parametrize(
    "updates",
    [
        {"modelSeeds": []},
        {"modelSeeds": [True]},
        {"modelSeeds": [1, 1]},
        {"modelSeeds": [-1]},
        {"name": ""},
        {"version": 9},
        {"dialect": "server"},
        {"sequences": []},
        {
            "sequences": [
                {"protein": {"id": "A", "sequence": "ACDE"}},
                {"ligand": {"id": "A", "smiles": "CCO"}},
            ]
        },
    ],
)
def test_af3_rejects_malformed_native_jobs(tmp_path, updates):
    path = tmp_path / "job.json"
    _write_json(path, _native_document(**updates))
    assert not AF3Prediction().validate_inputs({"af3_input": path})[0]


def test_af3_prepared_inputs_require_explicit_msas(tmp_path):
    document = _native_document()
    del document["sequences"][0]["protein"]["pairedMsa"]
    path = tmp_path / "job.json"
    _write_json(path, document)
    method = AF3Prediction(config={"run_data_pipeline": False})
    assert not method.validate_inputs({"af3_input": path})[0]


def _msa(chain, sequence, hit=None):
    return File.inline(
        f">{chain}\n{sequence}\n>hit\n{hit or sequence}\n",
        "a3m",
        metadata={"chain_id": chain},
    )


@pytest.mark.parametrize("fasta", [False, True])
def test_af3_multiple_chains_use_supplied_msa_and_disable_search(
    tmp_path, fake_apps, monkeypatch, fasta
):
    task = _task(tmp_path, AF3Prediction, ligands=["CCO"])
    task.inputs["proteins"] = ">A\nACDE\n>B\nGGGG\n" if fasta else ["ACDE", "GGGG"]
    task.inputs["msa"] = [_msa("B", "GGGG"), _msa("A", "ACDE")]
    commands = []

    def capture(self, arguments, directory, label):
        commands.append(arguments)
        _fake_command(self, arguments, directory, label)

    monkeypatch.setattr(ExternalPrediction, "_command", capture)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    summary = task.outputs["run_summary"].load()
    document = json.loads(
        (Path(summary["cases"][0]["directory"]) / "input.json").read_text()
    )
    assert [entity[next(iter(entity))]["id"] for entity in document["sequences"]] == [
        "A",
        "B",
        "C",
    ]
    for chain, sequence in zip(document["sequences"][:2], ("ACDE", "GGGG")):
        protein = chain["protein"]
        assert protein["unpairedMsa"].splitlines()[1] == sequence
        assert protein["pairedMsa"] == ""
        assert protein["templates"] == []
    assert "--run_data_pipeline=false" in commands[0]
    assert not task.method._searches(task.inputs)
    assert not any(
        label == "AF3 database"
        for label, _, _ in task.method._required_paths(task.inputs)
    )


@pytest.mark.parametrize("use_templates", [False, True])
def test_af3_without_msa_searches_all_protein_chains(
    tmp_path, fake_apps, use_templates
):
    task = _task(tmp_path, AF3Prediction, {"use_templates": use_templates}, ["CCO"])
    task.inputs["proteins"] = ["ACDE", "GGGG"]
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    summary = task.outputs["run_summary"].load()
    document = json.loads(
        (Path(summary["cases"][0]["directory"]) / "input.json").read_text()
    )
    for entity in document["sequences"][:2]:
        protein = entity["protein"]
        assert "unpairedMsa" not in protein
        assert "pairedMsa" not in protein
        assert protein.get("templates") == (None if use_templates else [])
    assert "protein" in task.method._searches(task.inputs)
    assert ("templates" in task.method._searches(task.inputs)) == use_templates


def test_af3_native_msa_overrides_paths_templates_and_homomer_groups(
    tmp_path, fake_apps
):
    document = _native_document()
    protein = document["sequences"][0]["protein"]
    protein.update(
        id=["A", "B"],
        unpairedMsa=">old\nAAAA\n",
        pairedMsa=">old\nAAAA\n",
        unpairedMsaPath="missing_unpaired.a3m",
        pairedMsaPath="missing_paired.a3m",
        templates=[{"mmcifPath": "missing.cif"}],
    )
    document["sequences"][1]["ligand"]["id"] = "C"
    path = tmp_path / "native.json"
    _write_json(path, document)
    msas = [_msa("A", "ACDE", "AC-E"), _msa("B", "ACDE", "A-DE")]
    method = AF3Prediction()
    assert method.validate_inputs({"af3_input": path, "msa": msas}) == (True, [])
    task = Task("native_msa", method, {"af3_input": path, "msa": msas})
    task.work_dir = tmp_path / "native_work"
    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
    case_dir = Path(task.outputs["run_summary"].load()["cases"][0]["directory"])
    staged = json.loads((case_dir / "input.json").read_text())
    proteins = [
        entity["protein"] for entity in staged["sequences"] if "protein" in entity
    ]
    assert [protein["id"] for protein in proteins] == ["A", "B"]
    assert [protein["unpairedMsa"] for protein in proteins] == [
        msa.value for msa in msas
    ]
    assert all(
        protein["pairedMsa"] == "" and protein["templates"] == []
        for protein in proteins
    )
    assert all(
        "unpairedMsaPath" not in protein and "pairedMsaPath" not in protein
        for protein in proteins
    )
    assert json.loads(path.read_text()) == document


@pytest.mark.parametrize(
    "msas, message",
    [
        ([_msa("A", "ACDE")], "every protein chain"),
        ([_msa("A", "ACDE"), _msa("A", "ACDE")], "Duplicate MSA"),
        ([_msa("A", "ACDE"), _msa("C", "GGGG")], "does not match a protein chain"),
        ([_msa("A", "ACDE"), _msa("B", "AAAA")], "query sequence"),
    ],
)
def test_af3_rejects_incomplete_or_incorrect_supplied_msa(msas, message):
    valid, errors = AF3Prediction().validate_inputs(
        {"proteins": ["ACDE", "GGGG"], "ligands": ["CCO"], "msa": msas}
    )
    assert not valid
    assert message in " ".join(errors)


def test_af3_optional_templates_clear_native_template_references(tmp_path):
    document = _native_document()
    document["sequences"][0]["protein"]["templates"] = [{"mmcifPath": "missing.cif"}]
    path = tmp_path / "native.json"
    _write_json(path, document)
    assert not AF3Prediction().validate_inputs({"af3_input": path})[0]
    assert AF3Prediction(config={"use_templates": False}).validate_inputs(
        {"af3_input": path}
    ) == (True, [])


def test_af3_native_template_search_reuses_explicit_msa(tmp_path):
    document = _native_document()
    protein = document["sequences"][0]["protein"]
    protein.update(unpairedMsa=">A\nACDE\n", pairedMsa="")
    protein.pop("templates")
    path = tmp_path / "native.json"
    _write_json(path, document)
    method = AF3Prediction()
    inputs = {"af3_input": File.from_file(path, "json")}

    assert method._searches(method.coerce_inputs(inputs)[0]) == {"templates"}


def test_af3_worker_overrides_before_resolving_native_references(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from rapier.methods.prediction._worker import prepare_af3

    document = _native_document()
    document["sequences"][0]["protein"].update(
        unpairedMsaPath="missing.a3m",
        pairedMsaPath="missing.a3m",
        templates=[{"mmcifPath": "missing.cif"}],
    )
    path = tmp_path / "native.json"
    _write_json(path, document)
    seen = []

    def parse(payload, json_path):
        parsed = json.loads(payload)
        protein = parsed["sequences"][0]["protein"]
        assert "unpairedMsaPath" not in protein and "pairedMsaPath" not in protein
        assert protein["templates"] == []
        seen.append(json_path)
        return SimpleNamespace(to_json=lambda: json.dumps(parsed))

    monkeypatch.setitem(
        sys.modules,
        "alphafold3.common",
        SimpleNamespace(
            folding_input=SimpleNamespace(Input=SimpleNamespace(from_json=parse))
        ),
    )
    result = prepare_af3(
        {
            "source": str(path),
            "msa": {"A": ">A\nACDE\n"},
            "use_templates": False,
            "run_data_pipeline": False,
        }
    )
    assert seen == [path]
    assert result["document"]["sequences"][0]["protein"]["unpairedMsa"] == ">A\nACDE\n"


@pytest.mark.parametrize("method_class", METHODS)
def test_methods_return_validated_outputs_and_occurrence_ids(
    tmp_path, fake_apps, method_class
):
    config = {"num_diffusion_samples": 2} if method_class is AF3Prediction else {}
    task = _task(tmp_path, method_class, config)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    summary = task.outputs["run_summary"].load()
    assert (summary["requested"], summary["succeeded"], summary["failed"]) == (2, 2, 0)
    rows = task.outputs["scores"].rows()
    assert {int(row["input_occurrence"]) for row in rows} == {1, 2}
    if method_class is AF3Prediction:
        assert len(task.outputs["predicted_structures"]) == 4
        assert len(task.outputs["best_structures"]) == 2


@pytest.mark.parametrize("method_class", METHODS)
@pytest.mark.parametrize("strict", [False, True])
def test_partial_chemical_failures_are_explicit(
    tmp_path, fake_apps, method_class, strict
):
    task = _task(
        tmp_path,
        method_class,
        {"allow_partial_failures": not strict},
        ["invalid bad", "CCO good"],
    )
    expected = TaskStatus.FAILED if strict else TaskStatus.COMPLETED
    assert task.method.execute(task) == expected, task.error_message
    summary = json.loads((task.work_dir / "outputs" / "run_summary.json").read_text())
    assert (summary["succeeded"], summary["failed"]) == ((0, 2) if strict else (1, 1))
    assert "invalid ligand" in summary["cases"][0]["error"]
    if not strict:
        assert {row["id"] for row in task.outputs["scores"].rows()} == {"good"}


@pytest.mark.parametrize("method_class", METHODS)
def test_all_failed_tasks_retain_diagnostics(tmp_path, fake_apps, method_class):
    task = _task(tmp_path, method_class, ligands=["invalid bad"])
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "No ligand/job succeeded" in task.error_message
    assert not task.outputs
    assert (
        json.loads((task.work_dir / "outputs" / "run_summary.json").read_text())[
            "failed"
        ]
        == 1
    )


@pytest.mark.parametrize("method_class", METHODS)
def test_generic_runner_validates_once_and_collects_receipt(
    tmp_path, fake_apps, monkeypatch, method_class
):
    task = _task(tmp_path, method_class)
    calls = []
    original = method_class.validate_outputs

    def validator(self, inputs, outputs, ctx):
        calls.append(set(outputs))
        return original(self, inputs, outputs, ctx)

    monkeypatch.setattr(method_class, "validate_outputs", validator)
    task.method.get_command(task)
    result = run_task_file(task.work_dir / "task.json")
    assert result["status"] == "completed", result["error"]
    assert len(calls) == 1
    assert "scores" in read_outputs(task)


def test_af3_native_job_preserves_seeds(tmp_path, fake_apps):
    path = tmp_path / "native.json"
    _write_json(path, _native_document(modelSeeds=[7, 9]))
    task = Task(
        "native",
        AF3Prediction(config={"num_diffusion_samples": 1}),
        {"af3_input": path},
    )
    task.work_dir = tmp_path / "work"
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert {int(row["seed"]) for row in task.outputs["scores"].rows()} == {7, 9}


@pytest.mark.parametrize("method_class", METHODS)
def test_corrupt_outputs_and_summary_are_reported_independently(
    tmp_path, fake_apps, method_class
):
    task = _task(tmp_path, method_class)
    assert task.method.execute(task) == TaskStatus.COMPLETED
    task.outputs["scores"].path.write_text("id,input_occurrence\nunknown,1\n")
    path = task.outputs["run_summary"].path
    summary = json.loads(path.read_text())
    summary["requested"] = 100
    _write_json(path, summary)
    errors = task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )
    assert any("counts" in error for error in errors)
    assert any("scores" in error for error in errors)


@pytest.mark.parametrize(
    "column,value",
    [
        ("Prediction", "nan"),
        ("Prediction", "1.1"),
        ("Predicted_Label", "0"),
        ("SMILES", "CCN"),
    ],
)
def test_drug_lamp_rejects_invalid_native_values(tmp_path, fake_apps, column, value):
    task = _task(tmp_path, DrugLAMP, ligands=["CCO"])
    assert task.method.execute(task) == TaskStatus.COMPLETED
    case = task.outputs["run_summary"].load()["cases"][0]
    path = Path(case["directory"]) / "predictions.csv"
    rows = list(csv.DictReader(path.open()))
    rows[0][column] = value
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )


def test_nesso_metadata_is_required_when_requested(tmp_path, fake_apps):
    task = _task(tmp_path, NessoPrediction, {"save_metadata": True})
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert len(task.outputs["prediction_tensors"]) == 2
    task.outputs["prediction_tensors"][0].path.write_bytes(b"bad")
    errors = task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )
    assert any("safetensors" in error for error in errors)


def test_safetensors_rejects_missing_keys_and_truncated_payload(tmp_path):
    path = tmp_path / "predictions.safetensors"
    _tensor_file(path)
    _validate_tensors(path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="payload"):
        _validate_tensors(path)
    _tensor_file(path, omit="pdistogram")
    with pytest.raises(KeyError):
        _validate_tensors(path)


@pytest.mark.parametrize("method_class", METHODS)
def test_retries_use_fresh_case_directories(tmp_path, fake_apps, method_class):
    task = _task(tmp_path, method_class)
    assert task.method.execute(task) == TaskStatus.COMPLETED
    first = task.outputs["run_summary"].load()["cases"][0]["directory"]
    assert task.method.execute(task) == TaskStatus.COMPLETED
    assert task.outputs["run_summary"].load()["cases"][0]["directory"] != first


def test_conda_commands_keep_paths_and_spaces_as_arguments():
    method = DrugLAMP(config={"conda_env": "/tmp/environment with spaces"})
    command = method._conda_command(
        ["python", "inference.py", "--checkpoint", "weights with spaces.ckpt"]
    )
    assert command[:5] == [
        "conda",
        "run",
        "--no-capture-output",
        "-p",
        "/tmp/environment with spaces",
    ]
    assert command[-1] == "weights with spaces.ckpt"
    assert method.env_requirements.conda_env == "/tmp/environment with spaces"


def test_nesso_binder_and_cpu_command(tmp_path, fake_apps, monkeypatch):
    commands = []

    def capture(self, arguments, directory, label):
        commands.append(arguments)
        document = yaml.safe_load((directory / "input.yaml").read_text())
        assert document["properties"] == [{"affinity": {"binder": "B"}}]
        _fake_command(self, arguments, directory, label)

    monkeypatch.setattr(ExternalPrediction, "_command", capture)
    task = _task(tmp_path, NessoPrediction, {"accelerator": "cpu"}, ["CCO"])
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert "32-true" in commands[0]
    assert "--no_kernels" in commands[0]


def test_missing_dependency_assets_are_actionable(tmp_path):
    method = DrugLAMP(
        config={
            "application_dir": str(tmp_path / "absent"),
            "checkpoint": str(tmp_path / "missing.ckpt"),
        }
    )
    task = _task(tmp_path, DrugLAMP)
    errors = method.check_dependencies(task.inputs, RunContext.for_task(task))
    assert any("DrugLAMP script" in error for error in errors)
    assert any("checkpoint" in error for error in errors)


@pytest.mark.parametrize("method_class", METHODS)
def test_long_inline_molecules_are_not_treated_as_paths(method_class):
    valid, errors = method_class().validate_inputs(
        {_protein_port(method_class): "ACDE", "ligands": ["C" * 300]}
    )
    assert valid, errors


@pytest.mark.parametrize("method_class", METHODS)
def test_strict_chemical_preflight_stops_before_inference(
    tmp_path, fake_apps, monkeypatch, method_class
):
    calls = []
    monkeypatch.setattr(
        ExternalPrediction, "_command", lambda *args: calls.append(args)
    )
    task = _task(
        tmp_path,
        method_class,
        {"allow_partial_failures": False},
        ["invalid bad", "CCO good"],
    )
    assert task.method.execute(task) == TaskStatus.FAILED
    assert not calls


@pytest.mark.parametrize(
    "corruption",
    ["missing_model", "missing_confidence", "bad_matrix", "wrong_best", "extra_model"],
)
def test_af3_invalid_case_does_not_hide_valid_ligands(
    tmp_path, fake_apps, monkeypatch, corruption
):
    def corrupt(self, arguments, directory, label):
        _fake_command(self, arguments, directory, label)
        if directory.name != "case_000002":
            return
        root = directory / "native" / "job"
        model = root / "seed-1_sample-0" / "job_seed-1_sample-0_model.cif"
        full_path = root / "seed-1_sample-0" / "job_seed-1_sample-0_confidences.json"
        if corruption == "missing_model":
            model.unlink()
        elif corruption == "missing_confidence":
            full_path.unlink()
        elif corruption == "bad_matrix":
            full = json.loads(full_path.read_text())
            full["pae"] = [[0]]
            _write_json(full_path, full)
        elif corruption == "wrong_best":
            (root / "job_model.cif").write_text(_cif(100))
        else:
            (root / "extra_model.cif").write_text(_cif(100))

    monkeypatch.setattr(ExternalPrediction, "_command", corrupt)
    task = _task(tmp_path, AF3Prediction, {"num_diffusion_samples": 2})
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert len(task.outputs["predicted_structures"]) == 2
    assert task.outputs["run_summary"].load()["failed"] == 1


@pytest.mark.parametrize("value", [None, float("nan"), 2, -1])
def test_nesso_rejects_missing_or_invalid_affinity_probability(
    tmp_path, fake_apps, value
):
    task = _task(tmp_path, NessoPrediction, ligands=["CCO"])
    assert task.method.execute(task) == TaskStatus.COMPLETED
    path = task.outputs["affinity_scores"][0].path
    data = json.loads(path.read_text())
    data["affinity_probability_binary"] = value
    _write_json(path, data)
    errors = task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )
    assert any("affinity_probability_binary" in error for error in errors)


def test_conditional_nesso_kernel_dependency():
    default = NessoPrediction()._probe_request()["modules"]
    enabled = NessoPrediction(config={"no_kernels": False})._probe_request()["modules"]
    assert "cuequivariance_torch" not in default
    assert "cuequivariance_torch" in enabled


@pytest.mark.parametrize("method_class", METHODS)
def test_invalid_environment_config_fails_early(method_class):
    with pytest.raises(ValueError, match="conda_env"):
        method_class(config={"conda_env": ""})


@pytest.mark.parametrize("method_class", METHODS)
def test_partial_malformed_sdf_records(tmp_path, method_class):
    path = tmp_path / "ligands.sdf"
    path.write_text(
        "good\n  Rapier\n\n  1  0  0  0  0  0            999 V2000\n"
        "    0.0000    0.0000    0.0000 C   0  0  0  0  0\nM  END\n$$$$\n"
        "bad\nnot an SDF\n$$$$\n"
    )
    inputs = {_protein_port(method_class): "ACDE", "ligands": path}
    assert method_class().validate_inputs(inputs)[0]
    assert not method_class(config={"allow_partial_failures": False}).validate_inputs(
        inputs
    )[0]


def test_standalone_worker_does_not_shadow_installed_nesso(tmp_path):
    package = tmp_path / "nesso"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text("__version__ = 'test-package'\n")
    from rapier.methods.prediction import _external

    worker = Path(_external.__file__).with_name("_worker.py")
    result = subprocess.run(
        [sys.executable, "-B", str(worker)],
        input=json.dumps({"action": "probe", "modules": ["nesso.main"]}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        check=True,
    )
    report = json.loads(result.stdout.split("RAPIER_RESULT=")[-1])
    assert not report["errors"], report
    assert report["versions"]["nesso.main"] == "test-package"


@pytest.mark.parametrize("method_class", METHODS)
def test_relative_local_work_directory(tmp_path, fake_apps, monkeypatch, method_class):
    monkeypatch.chdir(tmp_path)
    task = _task(tmp_path, method_class, ligands=["CCO"])
    task.work_dir = Path("local_run")
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert task.outputs["scores"].path.is_absolute()
