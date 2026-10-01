"""Integration tests for SLURM components.

Most tests use the dry-run SlurmClient or generate scripts without submitting.
Tests requiring a real SLURM installation are marked @pytest.mark.slurm and
skipped when sbatch is unavailable.
"""

import shutil
from pathlib import Path

import pytest

from rapier.slurm.client import SlurmClient
from rapier.slurm.job import SlurmJob, SlurmJobStatus
from rapier.slurm.script_generator import ScriptGenerator
from rapier.slurm.dependencies import DependencyBuilder
from rapier.slurm.resources import ResourceParser


SLURM_AVAILABLE = shutil.which("sbatch") is not None


class TestScriptGenerator:
    def test_generate_basic_script(self):
        gen = ScriptGenerator()
        job = SlurmJob(
            job_name="test_job",
            resources={"cpus": 4, "memory": "8GB", "time_limit": "1h"},
        )
        script = gen.generate_script(job, command="echo hello")
        assert "#!/bin/bash" in script or "#SBATCH" in script
        assert "test_job" in script
        assert "echo hello" in script

    def test_generate_includes_resources(self):
        gen = ScriptGenerator()
        job = SlurmJob(
            job_name="resourced",
            resources={"cpus": 8, "memory": "16GB", "time_limit": "2h"},
        )
        script = gen.generate_script(job, command="run.sh")
        assert "8" in script  # cpus
        assert "16384" in script or "16" in script  # memory in MB

    def test_generate_with_modules(self):
        gen = ScriptGenerator()
        job = SlurmJob(job_name="modjob", resources={"cpus": 1})
        script = gen.generate_script(
            job, command="vina", modules=["vina/1.2.0", "python/3.9"]
        )
        assert "vina/1.2.0" in script

    def test_write_script(self, tmp_path):
        gen = ScriptGenerator()
        job = SlurmJob(job_name="written", resources={"cpus": 1})
        script = gen.generate_script(job, command="echo hi")
        out_path = tmp_path / "job.sh"
        written = gen.write_script(script, out_path)
        assert written.exists()
        # Should be executable
        assert written.stat().st_mode & 0o111

    def test_array_job_script(self):
        gen = ScriptGenerator()
        job = SlurmJob(job_name="array", resources={"cpus": 1})
        script = gen.generate_array_job_script(
            job,
            command_template="python process.py --idx $SLURM_ARRAY_TASK_ID",
            array_size=10,
        )
        assert "1-10" in script
        assert "SLURM_ARRAY_TASK_ID" in script


class TestDependencyBuilder:
    def test_build_simple_dependency(self):
        builder = DependencyBuilder()
        jobs = [
            SlurmJob(job_name="a", job_id="12345"),
            SlurmJob(job_name="b", job_id="12346"),
        ]
        dep = builder.build_dependency_string(jobs, "afterok")
        assert dep == "afterok:12345:12346"

    def test_empty_jobs_returns_none(self):
        builder = DependencyBuilder()
        assert builder.build_dependency_string([], "afterok") is None

    def test_jobs_without_ids_returns_none(self):
        builder = DependencyBuilder()
        jobs = [SlurmJob(job_name="a")]  # no job_id
        assert builder.build_dependency_string(jobs, "afterok") is None

    def test_invalid_policy_raises(self):
        builder = DependencyBuilder()
        jobs = [SlurmJob(job_name="a", job_id="1")]
        with pytest.raises(ValueError):
            builder.build_dependency_string(jobs, "bogus")

    def test_failure_policy_mapping(self):
        builder = DependencyBuilder()
        jobs = [SlurmJob(job_name="a", job_id="1")]
        assert builder.build_from_failure_policy(jobs, "stop").startswith("afterok")
        assert builder.build_from_failure_policy(jobs, "continue").startswith("afterany")

    def test_complex_dependency(self):
        builder = DependencyBuilder()
        j1 = SlurmJob(job_name="a", job_id="1")
        j2 = SlurmJob(job_name="b", job_id="2")
        dep = builder.build_complex_dependency(afterok=[j1], afterany=[j2])
        assert "afterok:1" in dep
        assert "afterany:2" in dep
        assert "?" in dep

    def test_parse_dependency_string(self):
        builder = DependencyBuilder()
        parsed = builder.parse_dependency_string("afterok:12345:12346")
        assert parsed[0]["type"] == "afterok"
        assert parsed[0]["job_ids"] == ["12345", "12346"]

    def test_validate_dependency_string(self):
        builder = DependencyBuilder()
        assert builder.validate_dependency_string("afterok:123")
        assert not builder.validate_dependency_string("bogus:123")


class TestResourceParser:
    def test_parse_memory(self):
        parser = ResourceParser()
        assert parser.parse_memory("4GB") == 4096
        assert parser.parse_memory("512MB") == 512

    def test_parse_time_slurm_format(self):
        parser = ResourceParser()
        assert parser.parse_time("2:30:00") == 9000

    def test_parse_time_simple_format(self):
        parser = ResourceParser()
        assert parser.parse_time("1h") == 3600
        assert parser.parse_time("90m") == 5400

    def test_parse_cpus(self):
        parser = ResourceParser()
        assert parser.parse_cpus(4) == 4
        assert parser.parse_cpus("8") == 8

    def test_parse_cpus_invalid(self):
        parser = ResourceParser()
        with pytest.raises(ValueError):
            parser.parse_cpus(0)

    def test_parse_gpus_formats(self):
        parser = ResourceParser()
        assert parser.parse_gpus(2) == {"count": 2, "type": None}
        assert parser.parse_gpus("2xV100") == {"count": 2, "type": "V100"}
        assert parser.parse_gpus("V100:2") == {"count": 2, "type": "V100"}

    def test_format_time_for_slurm(self):
        parser = ResourceParser()
        assert parser.format_time_for_slurm(3661) == "01:01:01"
        assert parser.format_time_for_slurm(90000) == "1-01:00:00"

    def test_validate_resources_within_limits(self):
        parser = ResourceParser()
        ok, errors = parser.validate_resources(
            {"cpus": 4, "memory": "8GB"},
            limits={"max_cpus": 16, "max_memory_mb": 32768},
        )
        assert ok

    def test_validate_resources_exceeds_limit(self):
        parser = ResourceParser()
        ok, errors = parser.validate_resources(
            {"cpus": 100}, limits={"max_cpus": 16}
        )
        assert not ok


class TestSlurmClientDryRun:
    def test_submit_job_dry_run(self):
        client = SlurmClient(dry_run=True)
        job_id = client.submit_job(Path("/fake/script.sh"))
        assert job_id.isdigit()

    def test_get_job_status_dry_run(self):
        client = SlurmClient(dry_run=True)
        assert client.get_job_status("1000") == "COMPLETED"

    def test_cancel_job_dry_run(self):
        client = SlurmClient(dry_run=True)
        assert client.cancel_job("1000") is True

    def test_wait_for_jobs_dry_run(self):
        client = SlurmClient(dry_run=True)
        statuses = client.wait_for_jobs(["1000", "1001"])
        assert statuses == {"1000": "COMPLETED", "1001": "COMPLETED"}

    def test_unique_job_ids(self):
        client = SlurmClient(dry_run=True)
        id1 = client.submit_job(Path("/fake/a.sh"))
        id2 = client.submit_job(Path("/fake/b.sh"))
        assert id1 != id2


class TestSlurmJob:
    def test_status_properties(self):
        job = SlurmJob(job_name="t", status=SlurmJobStatus.COMPLETED, exit_code=0)
        assert job.succeeded
        assert job.is_finished
        assert not job.is_running

    def test_failed_property(self):
        job = SlurmJob(job_name="t", status=SlurmJobStatus.FAILED)
        assert job.failed

    def test_update_status(self):
        job = SlurmJob(job_name="t")
        job.update_status("RUNNING")
        assert job.status == SlurmJobStatus.RUNNING
        job.update_status("CD")  # SLURM short code for COMPLETED
        assert job.status == SlurmJobStatus.COMPLETED

    def test_dict_roundtrip(self):
        job = SlurmJob(job_name="t", job_id="999")
        data = job.to_dict()
        restored = SlurmJob.from_dict(data)
        assert restored.job_name == "t"
        assert restored.job_id == "999"


@pytest.mark.slurm
@pytest.mark.skipif(not SLURM_AVAILABLE, reason="SLURM (sbatch) not available")
class TestRealSlurm:
    def test_real_submission(self, tmp_path):
        gen = ScriptGenerator()
        job = SlurmJob(job_name="real_test", resources={"cpus": 1, "time_limit": "1m"})
        script_path = gen.generate_and_write(
            job, command="echo hello", output_path=tmp_path / "job.sh"
        )
        client = SlurmClient(dry_run=False)
        job_id = client.submit_job(script_path)
        assert job_id
