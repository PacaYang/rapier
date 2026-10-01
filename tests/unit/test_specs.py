"""Unit tests for specification classes."""

from pathlib import Path

import pytest

from rapier.core.specs import InputSpec, OutputSpec, EnvironmentSpec, ResourceSpec


class TestInputSpec:
    def test_defaults(self):
        spec = InputSpec()
        assert spec.required_files == []
        assert spec.optional_files == []
        assert spec.parameters == {}

    def test_missing_required_file(self):
        spec = InputSpec(required_files=["receptor"])
        is_valid, errors = spec.validate({})
        assert not is_valid
        assert any("receptor" in e for e in errors)

    def test_nonexistent_file(self, tmp_path):
        spec = InputSpec(required_files=["receptor"])
        is_valid, errors = spec.validate({"receptor": str(tmp_path / "nope.pdb")})
        assert not is_valid
        assert any("not found" in e for e in errors)

    def test_valid_file(self, tmp_path):
        f = tmp_path / "receptor.pdb"
        f.write_text("ATOM\n")
        spec = InputSpec(required_files=["receptor"])
        is_valid, errors = spec.validate({"receptor": str(f)})
        assert is_valid
        assert errors == []

    def test_required_parameter_missing(self):
        spec = InputSpec(parameters={"threshold": {"required": True}})
        is_valid, errors = spec.validate({})
        assert not is_valid
        assert any("threshold" in e for e in errors)


class TestOutputSpec:
    def test_missing_output(self, tmp_path):
        spec = OutputSpec(files=["results.sdf"])
        is_valid, errors = spec.validate(tmp_path)
        assert not is_valid
        assert any("results.sdf" in e for e in errors)

    def test_present_output(self, tmp_path):
        (tmp_path / "results.sdf").write_text("data")
        spec = OutputSpec(files=["results.sdf"])
        is_valid, errors = spec.validate(tmp_path)
        assert is_valid
        assert errors == []


class TestEnvironmentSpec:
    def test_missing_executable(self):
        spec = EnvironmentSpec(executables=["definitely_not_a_real_binary_xyz"])
        is_valid, errors = spec.validate()
        assert not is_valid
        assert any("definitely_not_a_real_binary_xyz" in e for e in errors)

    def test_present_executable(self):
        spec = EnvironmentSpec(executables=["python3"])
        is_valid, errors = spec.validate()
        # python3 should be on PATH in the test environment
        assert is_valid

    def test_missing_python_package(self):
        spec = EnvironmentSpec(python_packages=["package_that_does_not_exist_xyz"])
        is_valid, errors = spec.validate()
        assert not is_valid


class TestResourceSpec:
    def test_defaults(self):
        spec = ResourceSpec()
        assert spec.cpus == 1
        assert spec.memory == "4GB"
        assert spec.gpus == 0
        assert spec.nodes == 1

    def test_slurm_directives_basic(self):
        spec = ResourceSpec(cpus=4, memory="8GB", time="02:00:00")
        directives = spec.to_slurm_directives()
        joined = "\n".join(directives)
        assert "--cpus-per-task=4" in joined
        assert "--mem=8GB" in joined
        assert "--time=02:00:00" in joined

    def test_slurm_directives_gpu(self):
        spec = ResourceSpec(gpus=2, gpu_type="V100")
        joined = "\n".join(spec.to_slurm_directives())
        assert "--gres=gpu:V100:2" in joined

    def test_slurm_directives_gpu_no_type(self):
        spec = ResourceSpec(gpus=1)
        joined = "\n".join(spec.to_slurm_directives())
        assert "--gres=gpu:1" in joined

    def test_slurm_directives_partition(self):
        spec = ResourceSpec(partition="gpu")
        joined = "\n".join(spec.to_slurm_directives())
        assert "--partition=gpu" in joined
