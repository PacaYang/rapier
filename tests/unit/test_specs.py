"""Unit tests for specification classes."""

from pathlib import Path

import pytest

from rapier.core.specs import EnvironmentSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from rapier.data import BindingSite, Molecules, Poses, ProteinStructure


class TestInputSpec:
    def test_defaults(self):
        spec = InputSpec()
        assert spec.ports == {}
        assert spec.required == [] and spec.optional == []

    def test_ports_accept_data_classes_or_type_strings(self):
        spec = InputSpec({
            "receptor": PortSpec(ProteinStructure, "pdb"),
            "site": PortSpec("binding_site", "box", required=False),
        })
        assert spec.ports["receptor"].types == ("structure.protein",)
        assert spec.ports["site"].data_class is BindingSite
        assert spec.required == ["receptor"] and spec.optional == ["site"]

    def test_missing_required_port(self):
        spec = InputSpec({"receptor": PortSpec(ProteinStructure, "pdb")})
        is_valid, errors = spec.validate({})
        assert not is_valid
        assert any("receptor" in e for e in errors)

    def test_nonexistent_file(self, tmp_path):
        spec = InputSpec({"receptor": PortSpec(ProteinStructure, "pdb")})
        artifact = ProteinStructure.from_file(tmp_path / "nope.pdb")
        is_valid, errors = spec.validate({"receptor": artifact})
        assert not is_valid
        assert any("not found" in e for e in errors)

    def test_valid_file(self, tmp_path):
        f = tmp_path / "receptor.pdb"
        f.write_text("ATOM\n")
        spec = InputSpec({"receptor": PortSpec(ProteinStructure, "pdb")})
        is_valid, errors = spec.validate({"receptor": ProteinStructure.from_file(f)})
        assert is_valid
        assert errors == []

    def test_raw_paths_and_unknown_ports_are_rejected(self, tmp_path):
        spec = InputSpec({"receptor": PortSpec(ProteinStructure, "pdb")})
        is_valid, errors = spec.validate({"receptor": str(tmp_path / "r.pdb"), "extra": 1})
        assert not is_valid
        assert any("normalized Artifact" in e for e in errors)
        assert any("Unknown input port" in e for e in errors)

    def test_wrong_format(self, tmp_path):
        f = tmp_path / "receptor.cif"
        f.write_text("data_x\n")
        spec = InputSpec({"receptor": PortSpec(ProteinStructure, "pdb")})
        is_valid, errors = spec.validate({"receptor": ProteinStructure.from_file(f)})
        assert not is_valid
        assert any("structure.protein/cif" in e for e in errors)


class TestOutputSpec:
    def test_missing_output(self):
        spec = OutputSpec({"results": PortSpec(Molecules, "sdf")})
        is_valid, errors = spec.validate({})
        assert not is_valid
        assert any("results" in e for e in errors)

    def test_present_output(self, tmp_path):
        (tmp_path / "results.sdf").write_text("data")
        spec = OutputSpec({"results": PortSpec(Molecules, "sdf")})
        is_valid, errors = spec.validate({"results": Molecules.from_file(tmp_path / "results.sdf")})
        assert is_valid
        assert errors == []

    def test_subtype_satisfies_parent_port(self, tmp_path):
        (tmp_path / "poses.sdf").write_text("data")
        spec = OutputSpec({"results": PortSpec(Molecules, "sdf")})
        assert spec.validate({"results": Poses.from_file(tmp_path / "poses.sdf")})[0]

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
