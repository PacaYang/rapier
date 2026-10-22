"""Shared lifecycle for predictors running in isolated conda environments.

This module uses only Rapier and the standard library. Scientific imports live
in the standalone worker, which is executed by the application's interpreter.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from ...artifacts import Artifact
from ...core.method import Method, MethodError, RunContext
from ...core.specs import ConfigField, PortSpec, ResourceSpec
from ...data import Molecule, Molecules, ProteinSequence, Report, Scores, molecule_id

COMMON_CONFIG = {
    "allow_partial_failures": ConfigField(bool, default=True),
}
SCREENING_INPUTS = {
    "protein": PortSpec(ProteinSequence, ("inline", "fasta")),
    "ligands": PortSpec((Molecules, Molecule), ("smiles", "sdf"), cardinality="many"),
}
COMMON_OUTPUTS = {
    "scores": PortSpec(Scores, "csv"),
    "run_summary": PortSpec(Report, "json"),
}


def expanded(value):
    return Path(value).expanduser().resolve()


def read_json(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be numeric")
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return result


def protein_sequence(artifact, max_length=None):
    text = artifact.path.read_text() if artifact.is_file else str(artifact.value)
    if artifact.format == "fasta":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or not lines[0].startswith(">"):
            raise ValueError("protein FASTA must start with a header")
        if sum(line.startswith(">") for line in lines) != 1:
            raise ValueError("protein FASTA must contain exactly one record")
        text = "".join(lines[1:])
    sequence = re.sub(r"\s+", "", text).upper()
    if not sequence:
        raise ValueError("protein sequence cannot be empty")
    invalid = sorted(set(sequence) - set("ACDEFGHIKLMNPQRSTVWYBXZJUO"))
    if invalid:
        raise ValueError(
            "protein sequence has unsupported symbols: " + ", ".join(invalid)
        )
    if max_length is not None and len(sequence) > max_length:
        raise ValueError(f"protein sequence exceeds the {max_length}-residue limit")
    return sequence


def ligand_inventory(inputs):
    """Retain bad records and number duplicate IDs across all input artifacts."""
    cases, errors, occurrences = [], [], Counter()
    for artifact in inputs.get("ligands") or []:
        try:
            records = artifact.records()
            if not records:
                raise ValueError("contains no ligand records")
        except Exception as exc:
            errors.append(f"ligands: {exc}")
            continue
        for record in records:
            occurrences[record.id] += 1
            error = None
            if artifact.format == "smiles":
                if not record.smiles or any(char.isspace() for char in record.smiles):
                    error = "ligand must contain a nonempty SMILES token"
            elif not record.atoms or "M  END" not in (record.block or ""):
                error = "ligand SDF record has no readable atoms or M  END"
            cases.append(
                {
                    "id": record.id,
                    "input_occurrence": occurrences[record.id],
                    "case_id": f"case_{len(cases) + 1:06d}",
                    "smiles": record.smiles,
                    "block": record.block,
                    "error": error,
                }
            )
    if not cases:
        errors.append("ligands must contain at least one record")
    return cases, errors


def case_key(case):
    return case["id"], case["input_occurrence"]


def file_spec(port, path, **metadata):
    return {"port": port, "path": str(Path(path).resolve()), "metadata": metadata}


def rows_equal(actual, expected):
    """CSV round trips change scalar types, but must not change values."""
    if len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected):
        if set(left) != set(right):
            return False
        for key, value in right.items():
            received = left[key]
            if value is None:
                if received not in (None, ""):
                    return False
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    if not math.isclose(
                        float(received), value, rel_tol=1e-9, abs_tol=1e-9
                    ):
                        return False
                except (TypeError, ValueError):
                    return False
            elif str(received) != str(value):
                return False
    return True


class ExternalPrediction(Method):
    """Unregistered shared implementation for independent ligand predictions."""

    register_name = None
    inputs = SCREENING_INPUTS
    resources = ResourceSpec(
        cpus=8, memory="32GB", gpus=1, time="12:00:00", partition="gpu"
    )
    max_protein_length = None
    score_directions = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.env_requirements.conda_env = self.config["conda_env"]

    def _inventory(self, inputs):
        return ligand_inventory(inputs)

    def check_config(self, config):
        if not config["conda_env"].strip():
            return ["conda_env must be a nonempty environment name or prefix"]
        return []

    def validate_bindings(self, bound_ports):
        bound = set(bound_ports)
        return [
            f"{port} input is required"
            for port in ("protein", "ligands")
            if port not in bound
        ]

    def check_inputs(self, inputs: Dict[str, Artifact]) -> List[str]:
        errors = []
        try:
            protein_sequence(inputs["protein"], self.max_protein_length)
        except Exception as exc:
            errors.append(f"protein: {exc}")
        cases, inventory_errors = self._inventory(inputs)
        errors.extend(inventory_errors)
        bad = [
            f"ligand {case_key(case)}: {case['error']}"
            for case in cases
            if case.get("error")
        ]
        if (
            not self.config["allow_partial_failures"]
            or cases
            and len(bad) == len(cases)
        ):
            errors.extend(bad)
        return errors

    def _conda_command(self, arguments):
        environment = self.config["conda_env"]
        prefix = "-p" if "/" in environment or environment.startswith("~") else "-n"
        if prefix == "-p":
            environment = str(expanded(environment))
        return ["conda", "run", "--no-capture-output", prefix, environment] + list(
            arguments
        )

    def _process_environment(self):
        env = dict(os.environ)
        env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

    def _command(self, arguments, directory, label):
        """Execute without a shell; retain logs even when the application fails."""
        command = self._conda_command(arguments)
        with (directory / f"{label}.stdout.log").open("w") as stdout, (
            directory / f"{label}.stderr.log"
        ).open("w") as stderr:
            result = subprocess.run(
                command,
                cwd=str(directory),
                env=self._process_environment(),
                stdout=stdout,
                stderr=stderr,
            )
        if result.returncode:
            raise MethodError(
                f"{label} exited with status {result.returncode}; "
                f"see {directory / (label + '.stderr.log')}"
            )

    def _worker(self, request, directory=None):
        command = self._conda_command(
            ["python", "-B", str(Path(__file__).with_name("_worker.py"))]
        )
        result = subprocess.run(
            command,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=str(directory) if directory else None,
            env=self._process_environment(),
            timeout=120,
        )
        if directory:
            tag = request["action"]
            (directory / f"{tag}.stdout.log").write_text(result.stdout)
            (directory / f"{tag}.stderr.log").write_text(result.stderr)
        marker = "RAPIER_RESULT="
        reports = [
            line[len(marker) :]
            for line in result.stdout.splitlines()
            if line.startswith(marker)
        ]
        if result.returncode or not reports:
            raise MethodError(
                f"{self.display_name} {request['action']} probe failed: "
                f"{result.stderr[-2000:]}"
            )
        report = json.loads(reports[-1])
        if report.get("errors"):
            raise MethodError("; ".join(report["errors"]))
        return report

    def _probe_request(self):
        return {
            "action": "probe",
            "modules": self.env_requirements.python_packages,
            "executables": self.env_requirements.executables,
            "application_dir": self.config.get("application_dir"),
            "gpu": self._needs_gpu(),
            "tool": self.register_name,
        }

    def _needs_gpu(self):
        return True

    def validate_environment(self):
        if not shutil.which("conda"):
            return False, [
                "conda is required in PATH to run the selected application environment"
            ]
        try:
            self._worker(self._probe_request())
        except Exception as exc:
            return False, [f"{self.config['conda_env']}: {exc}"]
        return True, []

    def _required_paths(self, inputs):
        return []

    def _asset_request(self):
        return None

    def check_dependencies(self, inputs: Dict[str, Any], ctx: RunContext) -> List[str]:
        errors = []
        for label, path, directory in self._required_paths(inputs):
            path = expanded(path)
            try:
                if directory:
                    if not path.is_dir() or not os.access(str(path), os.R_OK | os.X_OK):
                        raise ValueError("directory is missing or unreadable")
                elif (
                    not path.is_file()
                    or path.stat().st_size == 0
                    or not os.access(str(path), os.R_OK)
                ):
                    raise ValueError("file is missing, empty, or unreadable")
            except (OSError, ValueError) as exc:
                errors.append(f"{label}: {path}: {exc}")
        request = self._asset_request()
        if not errors and request:
            try:
                self._runtime_assets = self._worker(request, ctx.work_dir)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def _ligand_options(self):
        return {}

    def _prepare_cases(self, cases, directory):
        report = self._worker(
            {"action": "ligands", "cases": cases, **self._ligand_options()}, directory
        )
        prepared = report["cases"]
        if [case_key(c) for c in prepared] != [case_key(c) for c in cases]:
            raise MethodError("Ligand preflight returned a different input inventory")
        return prepared

    def _predict_case(self, case, directory, protein):
        raise NotImplementedError

    def _collect_case(self, case, directory, protein):
        raise NotImplementedError

    def _additional_outputs(self, collected, ctx):
        return {}

    def _validate_additional_outputs(self, outputs, collected):
        return []

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        # Commands change their working directory; all generated paths must
        # therefore be absolute, including for local tasks with relative paths.
        ctx.output_dir = ctx.output_dir.resolve()
        cases, errors = self._inventory(inputs)
        if errors:
            raise MethodError("; ".join(errors))
        protein = (
            protein_sequence(inputs["protein"], self.max_protein_length)
            if inputs.get("protein")
            else None
        )
        attempt = ctx.output_dir / ("attempt_" + uuid.uuid4().hex)
        attempt.mkdir()
        cases = self._prepare_cases(cases, attempt)
        if not self.config["allow_partial_failures"] and any(
            case.get("error") for case in cases
        ):
            for case in cases:
                if not case.get("error"):
                    case["error"] = (
                        "Inference skipped because another ligand failed strict "
                        "input validation"
                    )
        results, collected, rows, files = [], [], [], []
        for case in cases:
            directory = attempt / case["case_id"]
            directory.mkdir()
            record = {key: value for key, value in case.items() if key != "block"}
            record["directory"] = str(directory)
            try:
                if case.get("error"):
                    raise ValueError(case["error"])
                self._predict_case(case, directory, protein)
                result = self._collect_case(case, directory, protein)
                collected.append(result)
                rows.extend(result["rows"])
                files.extend(result.get("files", []))
                record.update(status="succeeded", error=None)
            except Exception as exc:
                record.update(status="failed", error=str(exc))
            results.append(record)
        summary = {
            "method": self.register_name,
            "attempt_dir": str(attempt),
            "requested": len(cases),
            "succeeded": len(collected),
            "failed": len(cases) - len(collected),
            "cases": results,
        }
        summary_path = ctx.output_dir / "run_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        outputs = {
            "run_summary": Report.from_file(summary_path),
            "scores": Scores.from_records(
                rows, ctx.output_dir / "scores.csv", directions=self.score_directions
            ),
        }
        for descriptor in files:
            port = descriptor["port"]
            artifact = self.outputs[port].data_class.from_file(
                descriptor["path"], metadata=descriptor["metadata"]
            )
            outputs.setdefault(port, []).append(artifact)
        for port, spec in self.outputs.items():
            if spec.required and spec.cardinality == "many":
                outputs.setdefault(port, [])
        outputs.update(self._additional_outputs(collected, ctx))
        return outputs

    def validate_outputs(
        self, inputs: Dict[str, Any], outputs: Dict[str, Any], ctx: RunContext
    ) -> List[str]:
        errors, collected, expected_files, rows = [], [], [], []
        try:
            summary = outputs["run_summary"].load()
            requested, inventory_errors = self._inventory(inputs)
            errors.extend(inventory_errors)
            cases = summary["cases"]
            if summary["method"] != self.register_name:
                errors.append("run_summary method does not match the predictor")
            if [case_key(c) for c in cases] != [case_key(c) for c in requested]:
                errors.append(
                    "run_summary does not account for every requested input occurrence"
                )
            attempt = Path(summary["attempt_dir"]).resolve()
            if (
                attempt.parent != ctx.output_dir.resolve()
                or not attempt.name.startswith("attempt_")
            ):
                errors.append("run_summary attempt directory is outside this task")
            for case, original in zip(cases, requested):
                if (
                    case.get("case_id") != original["case_id"]
                    or Path(case["directory"]).resolve()
                    != attempt / original["case_id"]
                    or case.get("smiles") != original.get("smiles")
                ):
                    errors.append("run_summary case identity/directory is inconsistent")
            succeeded = sum(c.get("status") == "succeeded" for c in cases)
            failed = sum(c.get("status") == "failed" for c in cases)
            if (
                (summary["requested"], summary["succeeded"], summary["failed"])
                != (
                    len(requested),
                    succeeded,
                    failed,
                )
                or succeeded + failed != len(cases)
                or any(
                    type(summary[key]) is not int
                    for key in ("requested", "succeeded", "failed")
                )
            ):
                errors.append("run_summary counts/statuses are inconsistent")
            if not succeeded:
                errors.append(
                    "No ligand/job succeeded; "
                    "see run_summary.json for individual failures"
                )
            if failed and not self.config["allow_partial_failures"]:
                errors.append(
                    f"{failed} ligand/job(s) failed with allow_partial_failures=False"
                )
            protein = (
                protein_sequence(inputs["protein"], self.max_protein_length)
                if inputs.get("protein")
                else None
            )
            for case in cases:
                if case.get("status") == "failed":
                    if not case.get("error"):
                        errors.append(
                            f"Failed input {case_key(case)} has no failure reason"
                        )
                    continue
                try:
                    result = self._collect_case(case, Path(case["directory"]), protein)
                    collected.append(result)
                    rows.extend(result["rows"])
                    expected_files.extend(result.get("files", []))
                except Exception as exc:
                    errors.append(f"{case_key(case)}: {exc}")
        except Exception as exc:
            errors.append(f"Could not validate run_summary: {exc}")
        try:
            if not rows_equal(outputs["scores"].rows(), rows):
                errors.append("scores rows do not match successful native results")
            columns = outputs["scores"].metadata.get("columns", {})
            if any(
                columns.get(name, {}).get("better") != direction
                for name, direction in self.score_directions.items()
            ):
                errors.append("scores metadata has inconsistent ranking directions")
        except Exception as exc:
            errors.append(f"Could not read scores: {exc}")
        file_ports = set(self.outputs) - {"scores", "run_summary", "predictions"}
        for port in file_ports:
            expected = [
                (item["path"], item["metadata"])
                for item in expected_files
                if item["port"] == port
            ]
            try:
                actual = [
                    (str(item.path.resolve()), item.metadata)
                    for item in outputs.get(port, [])
                ]
                if actual != expected:
                    errors.append(
                        f"{port} inventory/metadata does not match "
                        "successful native results"
                    )
            except Exception as exc:
                errors.append(f"Could not inspect {port}: {exc}")
        errors.extend(self._validate_additional_outputs(outputs, collected))
        return errors


def native_case(identifier):
    return {
        "id": molecule_id(identifier),
        "input_occurrence": 1,
        "case_id": "case_000001",
        "error": None,
    }
