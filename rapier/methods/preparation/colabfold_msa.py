"""Generate chain-specific unpaired MSAs through the ColabFold ticket API."""

from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
import tarfile
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from ... import __version__
from ...artifacts import Artifact
from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, EnvironmentSpec, PortSpec, ResourceSpec
from ...data import File, ProteinSequence, Report
from ..msa import protein_records, read_text, validate_a3m

_UNIREF = "uniref.a3m"
_ENVIRONMENT = "bfd.mgnify30.metaeuk30.smag30.a3m"


def _entries(text: str) -> List[Tuple[str, str]]:
    """Read an A3M block without changing insertion case or gap symbols."""
    entries: List[Tuple[str, str]] = []
    header: Optional[str] = None
    sequence: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            if header is not None:
                entries.append((header, "".join(sequence)))
            header, sequence = line[1:].strip(), []
            if not header:
                raise ValueError("A3M contains an empty record header")
        elif header is None:
            raise ValueError("A3M sequence appears before its header")
        else:
            sequence.append(line)
    if header is not None:
        entries.append((header, "".join(sequence)))
    if not entries or any(not sequence for _, sequence in entries):
        raise ValueError("A3M contains an empty alignment or record")
    return entries


def _query_blocks(text: str) -> Dict[int, List[Tuple[str, str]]]:
    """Split MMseqs result files at NUL-delimited query boundaries."""
    blocks = {}
    for block in text.split("\x00"):
        if not block.strip():
            continue
        entries = _entries(block)
        try:
            query_id = int(entries[0][0])
        except ValueError as exc:
            raise ValueError("ColabFold result query headers must be numeric") from exc
        if query_id in blocks:
            raise ValueError(f"Duplicate ColabFold result query {query_id}")
        blocks[query_id] = entries
    return blocks


def _alignments(payload: bytes, sequences: List[str], use_env: bool) -> Dict[int, str]:
    """Read expected archive members in memory; never extract server paths."""
    names = [_UNIREF] + ([_ENVIRONMENT] if use_env else [])
    files: Dict[str, Dict[int, List[Tuple[str, str]]]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.suffix != ".a3m":
                    continue
                if path.is_absolute() or ".." in path.parts or not member.isfile():
                    raise ValueError(f"Unsafe A3M archive member: {member.name}")
                if path.name not in names:
                    continue
                if path.name in files:
                    raise ValueError(f"Duplicate archive member: {path.name}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"Cannot read archive member: {member.name}")
                with handle:
                    files[path.name] = _query_blocks(handle.read().decode("utf-8"))
    except (tarfile.TarError, OSError, UnicodeError, ValueError) as exc:
        raise MethodError(f"Invalid ColabFold result archive: {exc}") from exc
    missing = set(names) - set(files)
    if missing:
        raise MethodError(
            f"ColabFold result archive is missing {', '.join(sorted(missing))}"
        )
    expected = set(range(101, 101 + len(sequences)))
    for name, blocks in files.items():
        if set(blocks) != expected:
            raise MethodError(
                f"ColabFold {name} query IDs do not match submitted queries"
            )
    alignments = {}
    for query_id, sequence in zip(sorted(expected), sequences):
        entries = []
        for name in names:
            records = files[name][query_id]
            block = "".join(f">{header}\n{value}\n" for header, value in records)
            validate_a3m(block, sequence)
            entries.extend(records if not entries else records[1:])
        alignment = "".join(f">{header}\n{value}\n" for header, value in entries)
        validate_a3m(alignment, sequence)
        alignments[query_id] = alignment
    return alignments


class ColabFoldMSA(Method):
    """Submit protein chains to ColabFold and emit one unpaired A3M per chain."""

    register_name = "colabfold_msa"
    display_name = "ColabFold MSA Server"
    category = "preparation"
    inputs = {
        "proteins": PortSpec(
            ProteinSequence,
            ("inline", "fasta"),
            cardinality="many",
            description="Protein sequences or multi-record FASTA; optional chain IDs",
        ),
    }
    outputs = {
        "msa": PortSpec(
            File,
            "a3m",
            cardinality="many",
            description="One unpaired MSA per protein chain",
        ),
        "run_summary": PortSpec(Report, "json"),
    }
    config_fields = {
        "msa_server_url": ConfigField(str, default="https://api.colabfold.com"),
        "use_env": ConfigField(bool, default=True),
        "use_filter": ConfigField(bool, default=True),
        "request_timeout": ConfigField((int, float), default=30, minimum=0.001),
        "poll_interval": ConfigField((int, float), default=5, minimum=0.001),
        "timeout": ConfigField((int, float), default=1800, minimum=0.001),
        "max_retries": ConfigField(int, default=5, minimum=0),
    }
    resources = ResourceSpec(cpus=1, memory="2GB", time="01:00:00", partition="cpu")
    environment = EnvironmentSpec()

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors = []
        try:
            if not protein_records(inputs.get("proteins")):
                errors.append("At least one protein sequence is required")
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append(f"Protein input: {exc}")
        endpoint = urlsplit(self.config["msa_server_url"])
        if endpoint.scheme not in ("http", "https") or not endpoint.netloc:
            errors.append("msa_server_url must be an absolute HTTP or HTTPS URL")
        if endpoint.query or endpoint.fragment:
            errors.append("msa_server_url must not contain a query or fragment")
        return errors

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MethodError("ColabFold MSA request timed out")
        return remaining

    def _pause(self, deadline: float, delay: float) -> None:
        time.sleep(min(delay, self._remaining(deadline)))
        self._remaining(deadline)

    def _request(
        self, path: str, deadline: float, data: Optional[bytes] = None
    ) -> bytes:
        url = self.config["msa_server_url"].rstrip("/") + "/" + path
        headers = {"User-Agent": f"rapier/{__version__}"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(self.config["max_retries"] + 1):
            timeout = min(self.config["request_timeout"], self._remaining(deadline))
            try:
                with urlopen(
                    Request(url, data=data, headers=headers), timeout=timeout
                ) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise MethodError(f"ColabFold HTTP {exc.code} for {path}") from exc
                error = exc
            except (URLError, OSError) as exc:
                error = exc
            if attempt == self.config["max_retries"]:
                raise MethodError(
                    f"ColabFold request failed after {attempt + 1} attempts: {error}"
                ) from error
            self._pause(deadline, min(2**attempt, self.config["poll_interval"]))
        raise AssertionError("Unreachable retry state")

    def _status(
        self, path: str, deadline: float, data: Optional[bytes] = None
    ) -> Dict[str, Any]:
        try:
            payload = json.loads(self._request(path, deadline, data))
        except (ValueError, UnicodeError) as exc:
            raise MethodError("ColabFold server returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
            raise MethodError("ColabFold server response is missing a status")
        return payload

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        records = protein_records(inputs["proteins"])
        sequences = list(dict.fromkeys(record["sequence"] for record in records))
        query_ids = {sequence: index + 101 for index, sequence in enumerate(sequences)}
        query = "".join(
            f">{query_ids[sequence]}\n{sequence}\n" for sequence in sequences
        )
        mode = "env" if self.config["use_env"] else "all"
        if not self.config["use_filter"]:
            mode = "env-nofilter" if self.config["use_env"] else "nofilter"
        request = urlencode({"q": query, "mode": mode}).encode("utf-8")
        started = time.monotonic()
        deadline = started + self.config["timeout"]
        for attempt in range(self.config["max_retries"] + 1):
            state = self._status("ticket/msa", deadline, request)
            if state["status"] not in ("UNKNOWN", "RATELIMIT"):
                break
            if attempt == self.config["max_retries"]:
                raise MethodError(
                    f"ColabFold submission retries exhausted: {state['status']}"
                )
            self._pause(deadline, self.config["poll_interval"])
        ticket = state.get("id")
        if state["status"] in ("ERROR", "MAINTENANCE"):
            raise MethodError(f"ColabFold server reported {state['status']}")
        if not isinstance(ticket, str) or not ticket:
            raise MethodError("ColabFold server response is missing a ticket ID")
        ticket_path = quote(ticket, safe="")
        while state["status"] in ("UNKNOWN", "PENDING", "RUNNING", "RATELIMIT"):
            self._pause(deadline, self.config["poll_interval"])
            state = self._status(f"ticket/{ticket_path}", deadline)
        if state["status"] != "COMPLETE":
            raise MethodError(
                f"ColabFold server reported {state['status']} for ticket {ticket}"
            )
        archive = self._request(f"result/download/{ticket_path}", deadline)
        alignments = _alignments(archive, sequences, self.config["use_env"])
        # Only write outputs after the complete batch has passed validation.
        artifacts = []
        for index, record in enumerate(records):
            query_id = query_ids[record["sequence"]]
            alignment = alignments[query_id]
            alignment = f">{record['chain_id']}\n" + alignment.split("\n", 1)[1]
            path = ctx.output_dir / f"msa_{index + 1:04d}_{record['chain_id']}.a3m"
            path.write_text(alignment)
            artifacts.append(
                File.from_file(path, "a3m", metadata=dict(record, query_id=query_id))
            )
        return {
            "msa": artifacts,
            "run_summary": {
                "status": "completed",
                "msa_server_url": self.config["msa_server_url"],
                "ticket_id": ticket,
                "mode": mode,
                "chain_count": len(records),
                "unique_sequence_count": len(sequences),
                "elapsed_seconds": time.monotonic() - started,
                "chains": [
                    {
                        "chain_id": record["chain_id"],
                        "query_id": query_ids[record["sequence"]],
                    }
                    for record in records
                ],
            },
        }

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext
    ) -> List[str]:
        errors = []
        records = protein_records(inputs["proteins"])
        artifacts = outputs.get("msa") or []
        if len(artifacts) != len(records):
            errors.append("MSA output count does not match protein chain count")
        for index, (artifact, record) in enumerate(zip(artifacts, records)):
            if (
                not isinstance(artifact, Artifact)
                or artifact.metadata.get("chain_id") != record["chain_id"]
            ):
                errors.append(f"MSA output {index} has incorrect chain metadata")
                continue
            try:
                validate_a3m(read_text(artifact), record["sequence"])
            except (ValueError, OSError, UnicodeError) as exc:
                errors.append(f"MSA output {index}: {exc}")
        try:
            summary = outputs["run_summary"].load()
            if not isinstance(summary, dict) or summary.get("status") != "completed":
                errors.append("run_summary must report completed status")
            elif summary.get("chain_count") != len(records):
                errors.append("run_summary chain_count does not match protein inputs")
        except (KeyError, AttributeError, OSError, ValueError) as exc:
            errors.append(f"Cannot read run_summary: {exc}")
        return errors
