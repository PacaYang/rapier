"""Boltz-2 biomolecular complex and affinity prediction method."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...artifacts import Artifact, ArtifactStorage
from ...core.enums import TaskStatus
from ...core.specs import (
    ConfigField,
    ConfigSpec,
    InputSpec,
    OutputSpec,
    PortSpec,
    ResourceSpec,
)
from ...core.task import Task
from ..base_implementations import SingleStepMethod
from ..utils import check_executable
from .boltz2_runner import build_input_yaml, run_manifest


class Boltz2Prediction(SingleStepMethod):
    """Predict biomolecular structures and optional protein-ligand affinity."""

    register_name = "boltz2"
    contract_version = 2

    def __init__(
        self,
        name: str = "boltz2",
        version: str = "2.0",
        parameters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(name=name, version=version, parameters=parameters)
        self.config_spec = ConfigSpec(fields={
            "cache": ConfigField((str, type(None)), default=None),
            "checkpoint": ConfigField((str, type(None)), default=None),
            "devices": ConfigField(int, default=1, minimum=1),
            "accelerator": ConfigField(str, default="gpu", choices=("gpu", "cpu", "tpu")),
            "recycling_steps": ConfigField(int, default=3, minimum=0),
            "sampling_steps": ConfigField(int, default=200, minimum=1),
            "diffusion_samples": ConfigField(int, default=1, minimum=1),
            "max_parallel_samples": ConfigField(int, default=5, minimum=1),
            "step_scale": ConfigField((int, float), default=1.5, minimum=0),
            "output_format": ConfigField(str, default="mmcif", choices=("mmcif", "pdb")),
            "num_workers": ConfigField(int, default=2, minimum=0),
            "preprocessing_threads": ConfigField(
                (int, type(None)),
                default=None,
                validator=lambda value: (
                    "must be >= 1 when set" if value is not None and value < 1 else None
                ),
            ),
            "method": ConfigField((str, type(None)), default=None),
            "affinity_binder": ConfigField((str, type(None)), default=None),
            "properties": ConfigField((list, type(None)), default=None),
            "constraints": ConfigField((list, type(None)), default=None),
            "use_msa_server": ConfigField(bool, default=True),
            "msa_server_url": ConfigField(str, default="https://api.colabfold.com"),
            "msa_pairing_strategy": ConfigField(str, default="greedy", choices=("greedy", "complete")),
            "single_sequence_mode": ConfigField(bool, default=False),
            "use_potentials": ConfigField(bool, default=False),
            "override": ConfigField(bool, default=True),
            "write_full_pae": ConfigField(bool, default=False),
            "write_full_pde": ConfigField(bool, default=False),
            "subsample_msa": ConfigField(bool, default=False),
            "max_msa_seqs": ConfigField(int, default=8192, minimum=1),
            "num_subsampled_msa": ConfigField(int, default=1024, minimum=1),
            "no_kernels": ConfigField(bool, default=False),
            "sampling_steps_affinity": ConfigField(int, default=200, minimum=1),
            "diffusion_samples_affinity": ConfigField(int, default=5, minimum=1),
            "affinity_mw_correction": ConfigField(bool, default=False),
            "affinity_checkpoint": ConfigField((str, type(None)), default=None),
            # Optional handoff for downstream docking methods.  Prediction-only
            # callers keep the historical output contract unless this is enabled.
            "emit_docking_inputs": ConfigField(bool, default=False),
            "docking_box_padding": ConfigField((int, float), default=8.0, minimum=0),
        })
        self.prediction_params = self.normalize_config(parameters)

        self.input_spec = InputSpec(ports={
            "proteins": PortSpec("sequence.protein", ("inline", "fasta"), required=False, cardinality="many"),
            "dna": PortSpec("sequence.dna", ("inline", "fasta"), required=False, cardinality="many"),
            "rna": PortSpec("sequence.rna", ("inline", "fasta"), required=False, cardinality="many"),
            "ligands": PortSpec(
                ("molecule", "molecule.collection"), ("smiles", "sdf", "mol2", "ccd"),
                required=False, cardinality="many",
            ),
            "templates": PortSpec("structure", ("pdb", "cif"), required=False, cardinality="many"),
            "msas": PortSpec("file", ("a3m", "csv"), required=False, cardinality="many"),
            "constraints": PortSpec("file", "json", required=False),
        })
        self.output_spec = OutputSpec(ports={
            "predicted_structures": PortSpec("structure.complex", ("cif", "pdb"), cardinality="many"),
            "confidence_scores": PortSpec("report", "json", cardinality="many"),
            "affinity_scores": PortSpec("report", "json", required=False, cardinality="many"),
            "prediction_arrays": PortSpec("file", "npz", required=False, cardinality="many"),
            "run_summary": PortSpec("report", "json"),
            "vina_receptor": PortSpec(
                "structure.protein", "pdb", required=False,
                description="Receptor-only structure extracted from the selected Boltz complex",
            ),
            "docking_box": PortSpec(
                "binding_site", "box", required=False,
                description="Ligand-centered Vina search box extracted from Boltz coordinates",
            ),
        })
        self.env_requirements = {
            "software": ["boltz", "python"],
            "modules": [],
            "conda_env": None,
        }
        self.resource_spec = ResourceSpec(
            cpus=8,
            memory="32GB",
            gpus=1,
            time="12:00:00",
            partition="gpu",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not any(inputs.get(name) for name in ("proteins", "dna", "rna", "ligands")):
            errors.append("Boltz requires at least one entity input")
        for port_name in (
            "proteins", "dna", "rna", "ligands", "templates", "msas", "constraints"
        ):
            values = inputs.get(port_name, [])
            values = values if isinstance(values, list) else [values]
            for value in values:
                if not isinstance(value, Artifact):
                    errors.append(f"Input '{port_name}' must contain normalized Artifacts")
                    continue
                if value.is_file and not value.path.exists():
                    errors.append(f"Input '{port_name}' file not found: {value.path}")
                if port_name == "ligands" and value.format == "sdf":
                    try:
                        import rdkit  # noqa: F401
                    except ImportError:
                        errors.append("Boltz SDF ligand inputs require the RDKit package")
        affinity_binder = self.prediction_params.get("affinity_binder")
        if affinity_binder and not inputs.get("ligands"):
            errors.append("affinity_binder requires at least one ligand input")
        if self.prediction_params.get("emit_docking_inputs"):
            if not inputs.get("proteins"):
                errors.append("emit_docking_inputs requires at least one protein input")
            if not inputs.get("ligands"):
                errors.append("emit_docking_inputs requires at least one ligand input")
        return not errors, errors

    def validate_environment(self) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        if not check_executable("boltz"):
            errors.append("Boltz CLI (boltz) not found in PATH; install boltz[cuda] or boltz")
        if not check_executable("python"):
            errors.append("Python not found in PATH")
        if self.prediction_params.get("emit_docking_inputs"):
            try:
                import gemmi  # noqa: F401
            except ImportError:
                errors.append(
                    "Gemmi is required when emit_docking_inputs is enabled; install gemmi"
                )
        return not errors, errors

    def prepare(self, task: Task) -> None:
        work_dir = Path(task.work_dir or (Path.cwd() / "runs" / task.task_id))
        work_dir.mkdir(parents=True, exist_ok=True)
        task.work_dir = work_dir
        input_yaml = work_dir / "boltz2_input.yaml"
        config = dict(self.prediction_params)
        document = build_input_yaml(task.inputs, config, input_yaml)
        output_dir = work_dir / "boltz2_output"
        entity_mapping = {"polymer_ids": [], "ligand_ids": []}
        for entity in document.get("sequences", []):
            for entity_type, payload in entity.items():
                if not isinstance(payload, dict):
                    continue
                ids = payload.get("id", [])
                ids = ids if isinstance(ids, (list, tuple)) else [ids]
                target = "ligand_ids" if entity_type == "ligand" else "polymer_ids"
                entity_mapping[target].extend(str(item) for item in ids if item)
        manifest = {
            "manifest_version": 1,
            "work_dir": str(work_dir),
            "input_yaml": str(input_yaml),
            "output_dir": str(output_dir),
            "sample_name": input_yaml.stem,
            "config": config,
            "entity_mapping": entity_mapping,
        }
        manifest_path = work_dir / "boltz2_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        task.intermediate_outputs = task.intermediate_outputs or {}
        task.intermediate_outputs["boltz2_manifest"] = str(manifest_path)
        task.intermediate_outputs["boltz2_input_yaml"] = str(input_yaml)

    def get_command(self, task: Task) -> str:
        self.prepare(task)
        manifest_path = Path(task.intermediate_outputs["boltz2_manifest"]).resolve()
        return shlex.join([
            sys.executable,
            "-m",
            "rapier.methods.prediction.boltz2_runner",
            "--manifest",
            str(manifest_path),
        ])

    def execute(self, task: Task) -> TaskStatus:
        try:
            self.prepare(task)
            run_manifest(Path(task.intermediate_outputs["boltz2_manifest"]))
            self.collect_outputs(task)
            return TaskStatus.COMPLETED
        except Exception as exc:
            task.error_message = f"Boltz-2 prediction failed: {exc}"
            return TaskStatus.FAILED

    def collect_outputs(self, task: Task) -> Dict[str, Any]:
        summary_path = Path(task.work_dir) / "outputs" / "boltz2_run.json"
        if not summary_path.exists():
            raise FileNotFoundError("Boltz run summary not found")
        summary = json.loads(summary_path.read_text())
        structures = [Path(path) for path in summary.get("structures", [])]
        confidence = [Path(path) for path in summary.get("confidence", [])]
        affinity = [Path(path) for path in summary.get("affinity", [])]
        arrays = [Path(path) for path in summary.get("arrays", [])]
        for path in structures + confidence + affinity + arrays:
            if not path.exists():
                raise FileNotFoundError(f"Boltz output file not found: {path}")
        output_metadata = {
            "prediction_dir": summary.get("prediction_dir"),
            "model": "boltz2",
        }
        task.outputs = {
            "predicted_structures": [
                Artifact(
                    "structure.complex",
                    "pdb" if path.suffix == ".pdb" else "cif",
                    path,
                    metadata=output_metadata,
                )
                for path in structures
            ],
            "confidence_scores": [
                Artifact("report", "json", path, metadata=output_metadata) for path in confidence
            ],
            "run_summary": Artifact("report", "json", summary_path, metadata=output_metadata),
        }
        if affinity:
            task.outputs["affinity_scores"] = [
                Artifact("report", "json", path, metadata=output_metadata) for path in affinity
            ]
        if arrays:
            task.outputs["prediction_arrays"] = [
                Artifact("file", "npz", path, metadata=output_metadata) for path in arrays
            ]
        if self.prediction_params.get("emit_docking_inputs"):
            receptor_path = summary.get("docking_receptor")
            docking_box = summary.get("docking_box")
            if not receptor_path or not docking_box:
                raise FileNotFoundError(
                    "Boltz did not produce docking inputs; expected a receptor and docking box"
                )
            receptor = Path(receptor_path)
            if not receptor.exists():
                raise FileNotFoundError(f"Boltz receptor output not found: {receptor}")
            task.outputs["vina_receptor"] = Artifact(
                "structure.protein", "pdb", receptor, metadata=output_metadata
            )
            task.outputs["docking_box"] = Artifact(
                "binding_site", "box", docking_box,
                storage=ArtifactStorage.INLINE,
                metadata=output_metadata,
            )
        return task.outputs

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "Boltz2Prediction":
        return cls(
            name=config.get("name", "boltz2"),
            version=config.get("version", "2.0"),
            parameters=config.get("parameters", config.get("config", {})),
        )


# Short import alias for callers that refer to the method simply as Boltz2.
Boltz2 = Boltz2Prediction


class Boltz2PredictionMethod(Boltz2Prediction):
    """Compatibility registration using the descriptive method type name."""

    register_name = "boltz2_prediction"
