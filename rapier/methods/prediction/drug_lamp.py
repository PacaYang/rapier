"""DrugLAMP drug-target interaction prediction."""

import csv

from ...core.specs import ConfigField, EnvironmentSpec, PortSpec
from ...data import Table, write_rows
from ._external import (
    COMMON_CONFIG,
    COMMON_OUTPUTS,
    ExternalPrediction,
    expanded,
    number,
    rows_equal,
)


class DrugLAMP(ExternalPrediction):
    """Predict drug-target interaction probabilities with DrugLAMP."""

    register_name = "drug_lamp"
    display_name = "DrugLAMP"
    category = "prediction"
    outputs = {**COMMON_OUTPUTS, "predictions": PortSpec(Table, "csv")}
    environment = EnvironmentSpec(
        conda_env="drug_lamp_test",
        executables=["python"],
        python_packages=[
            "torch",
            "dgl",
            "dgllife",
            "torch_geometric",
            "pytorch_lightning",
            "rdkit",
            "numpy",
            "pandas",
            "transformers",
            "esm",
            "einops",
            "yacs",
            "tqdm",
            "networkx",
            "scipy",
            "sklearn",
            "run_prediction_DrugLAMP",
        ],
    )
    config_fields = {
        **COMMON_CONFIG,
        "conda_env": ConfigField(str, default="drug_lamp_test"),
        "application_dir": ConfigField(str, default="~/Applications/DrugLAMP"),
        "checkpoint": ConfigField(
            str,
            default="~/Applications/model_weights/DrugLAMP/max_val_ausum= 1.84364.ckpt",
        ),
        "hf_cache": ConfigField(str, default="~/.cache/huggingface/hub"),
        "torch_home": ConfigField(str, default="~/.cache/torch"),
        "model": ConfigField(
            str,
            default="DrugLAMP",
            choices=("DrugLAMP", "DrugLAMP2C2P", "DrugLAMPwoLLM"),
        ),
        "n_layer": ConfigField(int, default=30, choices=(12, 30, 33, 36, 48)),
        "device": ConfigField(str, default="cuda", choices=("cuda", "cpu")),
        "batch_size": ConfigField(int, default=1, minimum=1),
    }
    max_protein_length = 1022
    score_directions = {"interaction_probability": "higher"}

    def check_inputs(self, inputs):
        errors = super().check_inputs(inputs)
        try:
            from ._external import protein_sequence

            if "J" in protein_sequence(inputs["protein"], self.max_protein_length):
                errors.append("DrugLAMP protein alphabet does not support J")
        except Exception:
            pass  # The shared input check already reports this error.
        return errors

    def _needs_gpu(self):
        return self.config["device"] == "cuda"

    def _process_environment(self):
        env = super()._process_environment()
        env.update(
            HF_HUB_CACHE=str(expanded(self.config["hf_cache"])),
            TRANSFORMERS_CACHE=str(expanded(self.config["hf_cache"])),
            TORCH_HOME=str(expanded(self.config["torch_home"])),
        )
        return env

    def _required_paths(self, inputs):
        application = expanded(self.config["application_dir"])
        return [
            ("DrugLAMP script", application / "run_prediction_DrugLAMP.py", False),
            (
                "DrugLAMP model configuration",
                application / "configs" / (self.config["model"] + ".yaml"),
                False,
            ),
            ("DrugLAMP checkpoint", self.config["checkpoint"], False),
        ]

    def _asset_request(self):
        return {
            "action": "drug_lamp_assets",
            **{
                key: self.config[key]
                for key in (
                    "application_dir",
                    "checkpoint",
                    "hf_cache",
                    "torch_home",
                    "model",
                    "n_layer",
                )
            },
        }

    def _ligand_options(self):
        max_atoms = self._runtime_assets["max_atoms"]
        return {"max_atoms": max_atoms, "max_smiles_length": max_atoms}

    def _predict_case(self, case, directory, protein):
        write_rows(
            [{"SMILES": case["prepared_smiles"], "Protein": protein}],
            directory / "input.csv",
        )
        self._command(
            [
                "python",
                "-B",
                str(
                    expanded(self.config["application_dir"])
                    / "run_prediction_DrugLAMP.py"
                ),
                "--checkpoint",
                str(expanded(self.config["checkpoint"])),
                "--input",
                str(directory / "input.csv"),
                "--output",
                str(directory / "predictions.csv"),
                "--model",
                self.config["model"],
                "--n-layer",
                str(self.config["n_layer"]),
                "--device",
                self.config["device"],
                "--batch-size",
                str(self.config["batch_size"]),
                "--temp-dir",
                str(directory / "temporary"),
            ],
            directory,
            "drug_lamp",
        )

    def _collect_case(self, case, directory, protein):
        with (directory / "predictions.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 1:
            raise ValueError(
                "DrugLAMP expected one prediction for this ligand, "
                f"received {len(rows)}"
            )
        row = rows[0]
        if (
            row.get("SMILES") != case["prepared_smiles"]
            or row.get("Protein") != protein
        ):
            raise ValueError(
                "DrugLAMP output SMILES/protein does not match the requested pair"
            )
        probability = number(row["Prediction"], "DrugLAMP Prediction", 0, 1)
        label = number(row["Predicted_Label"], "DrugLAMP Predicted_Label", 0, 1)
        if label not in (0, 1) or label != int(probability > 0.5):
            raise ValueError(
                "DrugLAMP Predicted_Label disagrees with probability > 0.5"
            )
        identity = {"id": case["id"], "input_occurrence": case["input_occurrence"]}
        return {
            "rows": [
                {
                    **identity,
                    "interaction_probability": probability,
                    "predicted_label": int(label),
                }
            ],
            "native_rows": [row],
        }

    def _additional_outputs(self, collected, ctx):
        rows = [row for result in collected for row in result["native_rows"]]
        return {
            "predictions": Table.from_rows(
                rows,
                ctx.output_dir / "predictions.csv",
                columns=["SMILES", "Protein", "Prediction", "Predicted_Label"],
            )
        }

    def _validate_additional_outputs(self, outputs, collected):
        try:
            expected = [row for result in collected for row in result["native_rows"]]
            if not rows_equal(outputs["predictions"].rows(), expected):
                return [
                    "predictions CSV does not match successful native DrugLAMP results"
                ]
        except Exception as exc:
            return [f"Could not read predictions CSV: {exc}"]
        return []
