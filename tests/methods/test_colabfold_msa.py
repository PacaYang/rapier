"""Hermetic ColabFold ticket API and chain-specific output tests."""

import io
import json
import tarfile
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

import pytest

from rapier.core.enums import TaskStatus
from rapier.core.method import MethodError, RunContext
from rapier.core.task import Task
from rapier.data import ProteinSequence
from rapier.methods import ColabFoldMSA
from rapier.methods.preparation import colabfold_msa


@pytest.fixture(autouse=True)
def no_network_and_no_waiting(monkeypatch):
    clock = [0.0]

    def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(
        colabfold_msa, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )

    def unexpected_network(*args, **kwargs):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(colabfold_msa, "urlopen", unexpected_network)
    return clock


def _archive(uniref=None, environment=None, extra=None):
    files = {
        "uniref.a3m": uniref if uniref is not None else ">101\nACDE\n>uni\nAC-E\n\x00",
        "bfd.mgnify30.metaeuk30.smag30.a3m": (
            environment if environment is not None else ">101\nACDE\n>env\nACdDE\n\x00"
        ),
    }
    files.update(extra or {})
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, value in files.items():
            if value is None:
                continue
            if isinstance(value, tarfile.TarInfo):
                archive.addfile(value)
                continue
            data = value.encode("utf-8")
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return result.getvalue()


def _transport(monkeypatch, *responses):
    calls = []
    replies = iter(responses)

    def urlopen(request, timeout):
        calls.append((request, timeout))
        response = next(replies)
        if isinstance(response, Exception):
            raise response
        data = (
            response
            if isinstance(response, bytes)
            else json.dumps(response).encode("utf-8")
        )
        return io.BytesIO(data)

    monkeypatch.setattr(colabfold_msa, "urlopen", urlopen)
    return calls


def _task(tmp_path, proteins=None, config=None):
    task = Task(
        task_id="colabfold_test",
        method=ColabFoldMSA(config=config),
        inputs={
            "proteins": proteins
            or [ProteinSequence.from_string("ACDE", {"chain_id": "A"})]
        },
    )
    task.work_dir = tmp_path / "work"
    return task


def test_batch_deduplicates_queries_and_fans_out_chain_alignments(
    tmp_path, monkeypatch
):
    proteins = [
        ProteinSequence.from_string(sequence, {"chain_id": chain})
        for chain, sequence in (("A", "ACDE"), ("B", "FGHIK"), ("C", "ACDE"))
    ]
    archive = _archive(
        ">101\nACDE\n>uni_a\nAC-E\n\x00>102\nFGHIK\n>uni_b\nFGH-K\n\x00",
        ">101\nACDE\n>env_a\nACdDE\n\x00>102\nFGHIK\n>env_b\nFGhHIK\n\x00",
    )
    calls = _transport(
        monkeypatch,
        {"id": "ticket-1", "status": "PENDING"},
        {"status": "RUNNING"},
        {"status": "COMPLETE"},
        archive,
    )
    task = _task(tmp_path, proteins)

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message

    submission = calls[0][0]
    assert submission.full_url == "https://api.colabfold.com/ticket/msa"
    assert submission.get_method() == "POST"
    assert parse_qs(submission.data.decode()) == {
        "q": [">101\nACDE\n>102\nFGHIK\n"],
        "mode": ["env"],
    }
    assert submission.get_header("User-agent").startswith("rapier/")
    assert all(timeout <= 30 for _, timeout in calls)
    assert calls[-1][0].full_url.endswith("/result/download/ticket-1")
    outputs = task.outputs["msa"]
    assert [artifact.metadata["chain_id"] for artifact in outputs] == ["A", "B", "C"]
    assert [artifact.metadata["query_id"] for artifact in outputs] == [101, 102, 101]
    assert [artifact.metadata["source_artifact_id"] for artifact in outputs] == [
        artifact.artifact_id for artifact in task.inputs["proteins"]
    ]
    assert outputs[0].path.read_text() == ">A\nACDE\n>uni_a\nAC-E\n>env_a\nACdDE\n"
    assert outputs[2].path.read_text().startswith(">C\nACDE\n")
    assert len({artifact.path for artifact in outputs}) == 3
    summary = task.outputs["run_summary"].load()
    assert summary["chain_count"] == 3
    assert summary["unique_sequence_count"] == 2
    assert summary["ticket_id"] == "ticket-1"


def test_multirecord_fasta_matches_one_output_to_each_chain(tmp_path, monkeypatch):
    fasta = tmp_path / "chains.fasta"
    fasta.write_text(">A chain one\nACDE\n>B chain two\nACDE\n")
    _transport(monkeypatch, {"id": "ticket", "status": "COMPLETE"}, _archive())
    task = _task(tmp_path, [ProteinSequence.from_file(fasta)])

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert [artifact.metadata["chain_id"] for artifact in task.outputs["msa"]] == [
        "A",
        "B",
    ]
    assert [artifact.metadata["record_index"] for artifact in task.outputs["msa"]] == [
        0,
        1,
    ]


def test_inline_fasta_and_homomer_copies_use_the_same_many_contract(
    tmp_path, monkeypatch
):
    proteins = [
        ProteinSequence.inline(">A\nACDE\n>B\nACDE\n", "fasta"),
        ProteinSequence.from_string("ACDE", {"chain_ids": ["C", "D"]}),
    ]
    calls = _transport(monkeypatch, {"id": "ticket", "status": "COMPLETE"}, _archive())
    task = _task(tmp_path, proteins)

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert [artifact.metadata["chain_id"] for artifact in task.outputs["msa"]] == [
        "A",
        "B",
        "C",
        "D",
    ]
    assert parse_qs(calls[0][0].data.decode())["q"] == [">101\nACDE\n"]


@pytest.mark.parametrize(
    "use_env,use_filter,mode",
    [
        (True, True, "env"),
        (True, False, "env-nofilter"),
        (False, True, "all"),
        (False, False, "nofilter"),
    ],
)
def test_request_modes(tmp_path, monkeypatch, use_env, use_filter, mode):
    calls = _transport(monkeypatch, {"id": "ticket", "status": "COMPLETE"}, _archive())
    task = _task(tmp_path, config={"use_env": use_env, "use_filter": use_filter})

    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert parse_qs(calls[0][0].data.decode())["mode"] == [mode]
    text = task.outputs["msa"][0].path.read_text()
    assert (">env\n" in text) == use_env


def test_dependency_check_does_not_contact_server(tmp_path):
    task = _task(tmp_path)
    assert task.method.check_dependencies(task.inputs, RunContext.for_task(task)) == []


@pytest.mark.parametrize(
    "endpoint", ["file:///tmp/results", "relative", "https://example.com?x=1"]
)
def test_invalid_endpoint_fails_before_submission(tmp_path, endpoint):
    task = _task(tmp_path, config={"msa_server_url": endpoint})
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "msa_server_url" in task.error_message


def test_duplicate_chain_id_fails_before_submission(tmp_path):
    proteins = [
        ProteinSequence.from_string(sequence, {"chain_id": "A"})
        for sequence in ("ACDE", "FGHIK")
    ]
    task = _task(tmp_path, proteins)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "chain IDs must be unique" in task.error_message


def test_http_and_rate_limit_retries_are_bounded(tmp_path, monkeypatch):
    error = HTTPError("https://example.com/ticket/msa", 503, "unavailable", {}, None)
    calls = _transport(
        monkeypatch,
        error,
        {"status": "RATELIMIT"},
        {"id": "ticket", "status": "COMPLETE"},
        _archive(),
    )
    task = _task(tmp_path, config={"max_retries": 2})
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert len(calls) == 4


def test_transport_retry_exhaustion_has_no_outputs(tmp_path, monkeypatch):
    calls = _transport(
        monkeypatch, URLError("offline"), URLError("offline"), URLError("offline")
    )
    task = _task(tmp_path, config={"max_retries": 2})
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "3 attempts" in task.error_message
    assert len(calls) == 3
    assert not task.outputs


@pytest.mark.parametrize("status", ["UNKNOWN", "RATELIMIT"])
def test_submission_status_retry_exhaustion(tmp_path, monkeypatch, status):
    calls = _transport(monkeypatch, *[{"status": status}] * 3)
    task = _task(tmp_path, config={"max_retries": 2})
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "submission retries exhausted" in task.error_message
    assert len(calls) == 3


def test_poll_timeout_stops_and_publishes_nothing(tmp_path, monkeypatch):
    calls = _transport(
        monkeypatch,
        {"id": "ticket", "status": "PENDING"},
        {"status": "RUNNING"},
        {"status": "UNKNOWN"},
    )
    task = _task(tmp_path, config={"timeout": 3, "poll_interval": 1})
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "timed out" in task.error_message
    assert len(calls) == 3
    assert not task.outputs


@pytest.mark.parametrize("status", ["ERROR", "MAINTENANCE", "unexpected"])
def test_terminal_status_fails_batch(tmp_path, monkeypatch, status):
    _transport(monkeypatch, {"id": "ticket", "status": "PENDING"}, {"status": status})
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert status in task.error_message
    assert not task.outputs


@pytest.mark.parametrize(
    "response,message",
    [
        (b"not JSON", "invalid JSON"),
        ({"id": "ticket"}, "missing a status"),
        ({"status": "PENDING"}, "missing a ticket"),
    ],
)
def test_invalid_server_response(tmp_path, monkeypatch, response, message):
    _transport(monkeypatch, response)
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert message in task.error_message


@pytest.mark.parametrize(
    "archive,message",
    [
        (b"not a tarball", "Invalid ColabFold result archive"),
        (_archive(uniref=">101\nFGHI\n\x00"), "query"),
        (_archive(uniref=">102\nACDE\n\x00"), "query IDs"),
        (_archive(uniref=""), "query IDs"),
        (
            _archive(extra={"bfd.mgnify30.metaeuk30.smag30.a3m": None}),
            "missing",
        ),
        (_archive(uniref=">101\nACDE\n\x00>101\nACDE\n"), "Duplicate"),
        (_archive(extra={"../unsafe.a3m": ">101\nACDE\n"}), "Unsafe"),
    ],
)
def test_invalid_archive_fails_without_partial_files(
    tmp_path, monkeypatch, archive, message
):
    _transport(monkeypatch, {"id": "ticket", "status": "COMPLETE"}, archive)
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert message.lower() in task.error_message.lower()
    assert not task.outputs
    assert not list((task.work_dir / "outputs").glob("*.a3m"))


def test_archive_symlink_is_rejected(tmp_path, monkeypatch):
    member = tarfile.TarInfo("unsafe.a3m")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/passwd"
    _transport(
        monkeypatch,
        {"id": "ticket", "status": "COMPLETE"},
        _archive(extra={"unsafe.a3m": member}),
    )
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "Unsafe" in task.error_message


def test_http_client_does_not_retry_permanent_errors(tmp_path, monkeypatch):
    error = HTTPError("https://example.com/ticket/msa", 400, "bad request", {}, None)
    calls = _transport(monkeypatch, error)
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.FAILED
    assert "HTTP 400" in task.error_message
    assert len(calls) == 1


def test_output_validation_checks_content_and_metadata(tmp_path, monkeypatch):
    _transport(monkeypatch, {"id": "ticket", "status": "COMPLETE"}, _archive())
    task = _task(tmp_path)
    assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    task.outputs["msa"][0].path.write_text(">A\nFGHI\n")
    errors = task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )
    assert any("query" in error.lower() for error in errors)
    task.outputs["msa"][0].metadata["chain_id"] = "B"
    errors = task.method.validate_outputs(
        task.inputs, task.outputs, RunContext.for_task(task)
    )
    assert any("chain metadata" in error for error in errors)


def test_result_parser_rejects_sequence_before_header():
    with pytest.raises(MethodError, match="before its header"):
        colabfold_msa._alignments(_archive(uniref="ACDE\n>101\nACDE\n"), ["ACDE"], True)
