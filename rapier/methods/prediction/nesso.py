"""Nesso-1 protein-ligand binding affinity prediction."""

import json
import math
import struct

import yaml

from ...core.specs import ConfigField, EnvironmentSpec, PortSpec
from ...data import File, Report
from ._external import (
    COMMON_CONFIG,
    COMMON_OUTPUTS,
    ExternalPrediction,
    expanded,
    file_spec,
    number,
    read_json,
)


def _validate_tensors(path):
    """Validate the safetensors header and payload layout without importing Torch."""
    sizes = {
        "BOOL": 1,
        "U8": 1,
        "I8": 1,
        "I16": 2,
        "U16": 2,
        "F16": 2,
        "BF16": 2,
        "I32": 4,
        "U32": 4,
        "F32": 4,
        "I64": 8,
        "U64": 8,
        "F64": 8,
    }
    with path.open("rb") as handle:
        length_bytes = handle.read(8)
        if len(length_bytes) != 8:
            raise ValueError("Truncated safetensors header")
        length = struct.unpack("<Q", length_bytes)[0]
        if length > min(100_000_000, path.stat().st_size - 8):
            raise ValueError("Invalid safetensors header length")
        header = json.loads(handle.read(length))
    intervals = []
    for key, entry in header.items():
        if key == "__metadata__":
            continue
        shape = entry["shape"]
        if any(type(dim) is not int or dim < 0 for dim in shape):
            raise ValueError("Invalid safetensors shape")
        begin, end = entry["data_offsets"]
        count = math.prod(shape)
        if (
            type(begin) is not int
            or type(end) is not int
            or begin < 0
            or end - begin != count * sizes[entry["dtype"]]
        ):
            raise ValueError("Invalid safetensors data offsets")
        intervals.append((begin, end))
    position = 0
    for begin, end in sorted(intervals):
        if begin != position:
            raise ValueError("Overlapping or missing safetensors payload data")
        position = end
    if position != path.stat().st_size - 8 - length:
        raise ValueError("Truncated or unexpected safetensors payload data")
    shapes = {
        key: header[key]["shape"]
        for key in (
            "z",
            "pdistogram",
            "refine_mask",
            "pocket_mask",
            "token_pad_mask",
            "mol_type",
        )
    }
    z, dist = shapes["z"], shapes["pdistogram"]
    if (
        len(z) != 3
        or len(dist) != 3
        or not all(z)
        or not all(dist)
        or z[0] != z[1]
        or dist[:2] != z[:2]
    ):
        raise ValueError(
            "Nesso pair representations must be square and share token dimensions"
        )
    if (
        shapes["mol_type"] != [z[0]]
        or not shapes["refine_mask"]
        or shapes["refine_mask"] != shapes["pocket_mask"]
        or shapes["pocket_mask"] != shapes["token_pad_mask"]
        or len(shapes["refine_mask"]) != 1
        or shapes["refine_mask"][0] < z[0]
    ):
        raise ValueError("Nesso token masks and mol_type dimensions are inconsistent")


class NessoPrediction(ExternalPrediction):
    """Predict binding affinity and optional pair representations with Nesso-1."""

    register_name = "nesso"
    display_name = "Nesso-1"
    category = "prediction"
    version = "1.0.0"
    outputs = {
        **COMMON_OUTPUTS,
        "affinity_scores": PortSpec(Report, "json", cardinality="many"),
        "prediction_tensors": PortSpec(
            File, "safetensors", required=False, cardinality="many"
        ),
    }
    environment = EnvironmentSpec(
        conda_env="nesso",
        executables=["python", "nesso"],
        python_packages=[
            "nesso.main",
            "torch",
            "lightning",
            "rdkit",
            "numpy",
            "scipy",
            "transformers",
            "safetensors",
            "huggingface_hub",
            "einops",
            "mashumaro",
            "yaml",
            "click",
            "tqdm",
        ],
    )
    config_fields = {
        **COMMON_CONFIG,
        "conda_env": ConfigField(str, default="nesso"),
        "cache": ConfigField(str, default="~/Applications/model_weights/Nesso"),
        "checkpoint": ConfigField((str, type(None)), default=None),
        "ccd": ConfigField((str, type(None)), default=None),
        "model_revision": ConfigField(str, default="v1.0.0"),
        "accelerator": ConfigField(str, default="gpu", choices=("gpu", "cpu")),
        "precision": ConfigField((str, type(None)), default=None),
        "recycling_steps": ConfigField(int, default=5, minimum=1),
        "num_workers": ConfigField(int, default=2, minimum=1),
        "seed": ConfigField(int, default=42, minimum=0),
        "save_metadata": ConfigField(bool, default=False),
        "no_kernels": ConfigField(bool, default=True),
    }
    score_directions = {
        "affinity_pred_value": "lower",
        "affinity_probability_binary": "higher",
    }

    def _needs_gpu(self):
        return self.config["accelerator"] == "gpu"

    def _probe_request(self):
        request = super()._probe_request()
        request["modules"] = list(request["modules"])
        if not self.config["no_kernels"]:
            request["modules"].extend(
                ["cuequivariance_torch", "cuequivariance_ops_torch"]
            )
        return request

    def _process_environment(self):
        env = super()._process_environment()
        env["HF_HOME"] = str(expanded(self.config["cache"]) / "huggingface")
        env["HF_HUB_CACHE"] = env["HF_HOME"]
        return env

    def _required_paths(self, inputs):
        paths = [("Nesso cache", self.config["cache"], True)]
        if self.config["checkpoint"]:
            paths += [
                (
                    "Nesso weights",
                    expanded(self.config["checkpoint"]) / "model.safetensors",
                    False,
                ),
                (
                    "Nesso hyperparameters",
                    expanded(self.config["checkpoint"]) / "hparams.json",
                    False,
                ),
            ]
        if self.config["ccd"]:
            paths.append(("Nesso CCD", self.config["ccd"], False))
        return paths

    def _asset_request(self):
        return {
            "action": "nesso_assets",
            **{
                key: self.config[key]
                for key in ("cache", "checkpoint", "ccd", "model_revision")
            },
        }

    def _predict_case(self, case, directory, protein):
        document = {
            "sequences": [
                {"protein": {"id": "A", "sequence": protein}},
                {"ligand": {"id": "B", "smiles": case["prepared_smiles"]}},
            ],
            "properties": [{"affinity": {"binder": "B"}}],
        }
        self._worker({"action": "nesso_input", "document": document}, directory)
        (directory / "input.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
        command = [
            "nesso",
            "predict",
            str(directory / "input.yaml"),
            "--out_dir",
            str(directory / "native"),
            "--cache",
            str(expanded(self.config["cache"])),
            "--checkpoint",
            self._runtime_assets["checkpoint"],
            "--ccd",
            self._runtime_assets["ccd"],
            "--accelerator",
            self.config["accelerator"],
            "--devices",
            "1",
            "--precision",
            self.config["precision"]
            or ("32-true" if self.config["accelerator"] == "cpu" else "bf16-mixed"),
            "--recycling_steps",
            str(self.config["recycling_steps"]),
            "--num_workers",
            str(self.config["num_workers"]),
            "--seed",
            str(self.config["seed"]),
            "--require_affinity",
            "--override",
        ]
        if self.config["save_metadata"]:
            command.append("--save_metadata")
        if self.config["no_kernels"]:
            command.append("--no_kernels")
        self._command(command, directory, "nesso")

    def _collect_case(self, case, directory, protein):
        native = directory / "native" / "predictions" / "input"
        path = native / "affinity.json"
        data = read_json(path)
        value = number(data["affinity_pred_value"], "affinity_pred_value")
        probability = number(
            data["affinity_probability_binary"], "affinity_probability_binary", 0, 1
        )
        for key, item in data.items():
            number(
                item,
                key,
                0 if key.startswith("entropy_") else None,
                1 if key.startswith("entropy_") else None,
            )
        meta = {"id": case["id"], "input_occurrence": case["input_occurrence"]}
        files = [file_spec("affinity_scores", path, **meta)]
        if self.config["save_metadata"]:
            tensor_path = native / "predictions.safetensors"
            _validate_tensors(tensor_path)
            files.append(file_spec("prediction_tensors", tensor_path, **meta))
        return {
            "rows": [
                {
                    **meta,
                    **data,
                    "affinity_pred_value": value,
                    "affinity_probability_binary": probability,
                }
            ],
            "files": files,
        }
