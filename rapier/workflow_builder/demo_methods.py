"""Lightweight mock methods for exercising the workflow builder.

These methods declare realistic typed ports and configuration fields but do no
real science: ``execute`` writes small placeholder artifacts for every output
port. They live outside ``rapier.methods`` so ``auto_discover`` never picks
them up; call :func:`register_demo_methods` to opt in.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..artifacts import Artifact, ArtifactStorage
from ..core.enums import TaskStatus
from ..core.specs import ConfigField, ConfigSpec, InputSpec, OutputSpec, PortSpec, ResourceSpec
from ..core.task import Task
from ..methods.base_implementations import SingleStepMethod
from ..methods.registry import MethodRegistry, get_registry


# Placeholder file contents keyed by output format.
_PLACEHOLDERS = {
    "cif": "data_demo\n_entry.id DEMO\n",
    "pdb": "HEADER    DEMO STRUCTURE\nEND\n",
    "sdf": "demo\n  rapier\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n",
    "fasta": ">demo\nMKTAYIAKQR\n",
    "csv": "ligand,mode,affinity_kcal_mol\ndemo,1,-7.5\n",
    "a3m": ">query\nMKTAYIAKQR\n",
}


class DemoMethod(SingleStepMethod):
    """Base class for declarative mock methods.

    Subclasses set ``inputs``, ``outputs``, ``config_fields`` and optionally
    ``resources`` as class attributes.
    """

    category = "demo"
    inputs: Dict[str, PortSpec] = {}
    outputs: Dict[str, PortSpec] = {}
    config_fields: Dict[str, ConfigField] = {}
    resources = ResourceSpec(cpus=1, memory="2GB", time="00:10:00")
    contract_version = 2

    def __init__(self, name: Optional[str] = None, version: str = "0.1", parameters: Optional[Dict[str, Any]] = None):
        super().__init__(name=name or self.register_name, version=version, parameters=parameters)
        self.config_spec = ConfigSpec(fields=dict(self.config_fields))
        self.input_spec = InputSpec(ports=dict(self.inputs))
        self.output_spec = OutputSpec(ports=dict(self.outputs))
        self.resource_spec = self.resources
        self.normalize_config(parameters)

    def validate_inputs(self, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        return self.input_spec.validate(inputs)

    def validate_environment(self) -> Tuple[bool, List[str]]:
        return True, []

    def execute(self, task: Task) -> TaskStatus:
        output_dir = Path(task.work_dir or ".") / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: Dict[str, Any] = {}
        for port_name, port in self.output_spec.ports.items():
            artifact_type = port.types[0]
            artifact_format = port.formats[0] if port.formats else "json"
            if artifact_format in ("box", "inline"):
                value: Any = (
                    {"center": [0.0, 0.0, 0.0], "size": [20.0, 20.0, 20.0]}
                    if artifact_format == "box" else "MKTAYIAKQR"
                )
                artifact = Artifact(artifact_type, artifact_format, storage=ArtifactStorage.INLINE, value=value)
            else:
                path = output_dir / f"{port_name}.{artifact_format}"
                content = _PLACEHOLDERS.get(
                    artifact_format,
                    json.dumps({"method": self.name, "port": port_name, "config": self.config}, indent=2),
                )
                path.write_text(content)
                artifact = Artifact(artifact_type, artifact_format, path)
            outputs[port_name] = [artifact] if port.cardinality == "many" else artifact
        task.outputs = outputs
        return TaskStatus.COMPLETED

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "DemoMethod":
        return cls(
            name=config.get("name"),
            version=config.get("version", "0.1"),
            parameters=config.get("config") or config.get("parameters") or {},
        )


class DemoStructurePrediction(DemoMethod):
    """Predict a protein structure from sequence (mock)."""

    register_name = "demo_structure_prediction"
    display_name = "Structure Prediction"
    category = "prediction"
    inputs = {
        "sequence": PortSpec("sequence.protein", ("inline", "fasta"), description="Protein sequence to fold"),
        "msa": PortSpec("file", "a3m", required=False, description="Optional precomputed MSA"),
    }
    outputs = {
        "structure": PortSpec("structure.protein", "cif", description="Predicted structure"),
        "confidence": PortSpec("report", "json", description="pLDDT / pTM confidence scores"),
    }
    config_fields = {
        "num_recycles": ConfigField(int, default=3, minimum=0, maximum=20, description="Recycling iterations"),
        "num_models": ConfigField(int, default=1, minimum=1, maximum=5, description="Models to sample"),
        "use_templates": ConfigField(bool, default=False, description="Search structural templates"),
        "accelerator": ConfigField(str, default="gpu", choices=("gpu", "cpu"), description="Compute device"),
    }
    resources = ResourceSpec(cpus=4, memory="16GB", gpus=1, time="02:00:00", partition="gpu")


class DemoComplexPrediction(DemoMethod):
    """Co-fold a protein with a ligand and estimate affinity (mock)."""

    register_name = "demo_complex_prediction"
    display_name = "Complex Co-folding"
    category = "prediction"
    inputs = {
        "protein": PortSpec("sequence.protein", ("inline", "fasta"), description="Protein sequence"),
        "ligand": PortSpec(("molecule", "molecule.collection"), ("smiles", "sdf"), description="Ligand(s)"),
    }
    outputs = {
        "complex": PortSpec("structure.complex", "cif", description="Predicted protein-ligand complex"),
        "confidence": PortSpec("report", "json", description="Per-complex confidence"),
        "affinity": PortSpec("table", "csv", description="Predicted binding affinity"),
    }
    config_fields = {
        "diffusion_samples": ConfigField(int, default=1, minimum=1, maximum=25),
        "predict_affinity": ConfigField(bool, default=True),
        "seed": ConfigField(int, default=0, minimum=0),
    }
    resources = ResourceSpec(cpus=8, memory="32GB", gpus=1, time="04:00:00", partition="gpu")


class DemoSequenceDesign(DemoMethod):
    """Design sequences for a fixed backbone (mock)."""

    register_name = "demo_sequence_design"
    display_name = "Sequence Design"
    category = "design"
    inputs = {
        "backbone": PortSpec("structure", ("pdb", "cif"), description="Backbone to design against"),
    }
    outputs = {
        "sequences": PortSpec("sequence.protein", "fasta", cardinality="many", description="Designed sequences"),
        "scores": PortSpec("table", "csv", description="Per-design log-likelihood"),
    }
    config_fields = {
        "num_designs": ConfigField(int, default=8, minimum=1, maximum=1000),
        "temperature": ConfigField(float, default=0.1, minimum=0.0, maximum=2.0, description="Sampling temperature"),
        "fixed_residues": ConfigField(str, default="", description="Comma-separated residues to keep fixed"),
    }


class DemoPocketDetection(DemoMethod):
    """Detect ligandable pockets on a protein structure (mock)."""

    register_name = "demo_pocket_detection"
    display_name = "Pocket Detection"
    category = "preparation"
    inputs = {
        "structure": PortSpec("structure.protein", "pdb", description="Protein structure (PDB)"),
    }
    outputs = {
        "binding_site": PortSpec("binding_site", "box", description="Top-ranked pocket box"),
        "pockets": PortSpec("report", "json", description="All detected pockets"),
    }
    config_fields = {
        "min_volume": ConfigField((int, float), default=100.0, minimum=0, description="Minimum pocket volume (Å³)"),
        "padding": ConfigField((int, float), default=4.0, minimum=0, description="Box padding (Å)"),
    }


class DemoDocking(DemoMethod):
    """Dock a ligand library into a receptor pocket (mock)."""

    register_name = "demo_docking"
    display_name = "Docking"
    category = "docking"
    inputs = {
        "receptor": PortSpec("structure.protein", ("pdb", "cif"), description="Receptor structure"),
        "ligands": PortSpec("molecule.collection", ("sdf", "smiles"), description="Ligand library"),
        "binding_site": PortSpec("binding_site", "box", required=False, description="Search box; blind docking if unset"),
    }
    outputs = {
        "poses": PortSpec("molecule.collection", "sdf", description="Docked poses"),
        "scores": PortSpec("table", "csv", description="Docking scores"),
    }
    config_fields = {
        "exhaustiveness": ConfigField(int, default=8, minimum=1, maximum=64),
        "num_poses": ConfigField(int, default=9, minimum=1, maximum=50),
        "scoring": ConfigField(str, default="vina", choices=("vina", "vinardo", "ad4")),
    }
    resources = ResourceSpec(cpus=8, memory="8GB", time="01:00:00")


class DemoRescoring(DemoMethod):
    """Rescore docked poses and pick the best candidates (mock)."""

    register_name = "demo_rescoring"
    display_name = "Rescoring & Ranking"
    category = "analysis"
    inputs = {
        "poses": PortSpec("molecule.collection", "sdf", description="Poses to rescore"),
        "receptor": PortSpec("structure", ("pdb", "cif"), required=False, description="Receptor for interaction scoring"),
        "scores": PortSpec("table", "csv", required=False, description="Upstream scores to combine"),
    }
    outputs = {
        "ranked": PortSpec("table", "csv", description="Ranked candidates"),
        "top_poses": PortSpec("molecule.collection", "sdf", description="Top-N poses"),
        "summary": PortSpec("report", "json", description="Run summary"),
    }
    config_fields = {
        "top_n": ConfigField(int, default=10, minimum=1),
        "metric": ConfigField(str, default="consensus", choices=("consensus", "docking", "ml")),
        "minimize": ConfigField(bool, default=True, description="Lower scores are better"),
    }


DEMO_METHODS = (
    DemoStructurePrediction,
    DemoComplexPrediction,
    DemoSequenceDesign,
    DemoPocketDetection,
    DemoDocking,
    DemoRescoring,
)


def register_demo_methods(registry: Optional[MethodRegistry] = None) -> List[str]:
    """Register the demo methods, skipping any names already present."""
    registry = registry or get_registry()
    registered = []
    for method_class in DEMO_METHODS:
        if not registry.has(method_class.register_name):
            registry.register(method_class.register_name, method_class, {"demo": True})
            registered.append(method_class.register_name)
    return registered


def demo_pipeline() -> Dict[str, Any]:
    """Pipeline dict wiring the demo methods: fold -> pockets -> dock -> filter -> rescore."""
    return {
        "name": "demo_fold_and_dock",
        "description": "Fold a target, find its pocket, dock a library and rank the hits (mock methods)",
        "version": "1.0.0",
        "inputs": {
            "target_sequence": {"type": "sequence.protein", "format": "inline", "value": "MKTAYIAKQRQISFVKSHFSRQ"},
            "ligand_library": {
                "type": "molecule.collection", "format": "smiles",
                "value": ["CCO ethanol", "c1ccccc1O phenol", "CC(=O)Oc1ccccc1C(=O)O aspirin"],
            },
        },
        "methods": [
            {"id": "fold", "type": "demo_structure_prediction",
             "inputs": {"sequence": "pipeline.target_sequence"}, "config": {"num_recycles": 5}},
            {"id": "pockets", "type": "demo_pocket_detection", "inputs": {"structure": "fold.structure"}},
            {"id": "dock", "type": "demo_docking", "inputs": {
                "receptor": "fold.structure", "ligands": "pipeline.ligand_library",
                "binding_site": "pockets.binding_site"}},
            {"id": "filter_hits", "type": "molecule_filter", "inputs": {
                "molecules": "dock.poses", "scores": "dock.scores", "binding_site": "pockets.binding_site"},
             "config": {
                "criteria": [{"column": "affinity_kcal_mol", "op": "<=", "value": -7.0}],
                "position_mode": "fraction_in_box", "min_fraction_in_box": 0.8,
                "rank_by": "affinity_kcal_mol", "top_percent": 1}},
            {"id": "rescore", "type": "demo_rescoring", "inputs": {
                "poses": "filter_hits.filtered_molecules", "receptor": "fold.structure",
                "scores": "filter_hits.filtered_scores"}},
        ],
        "outputs": {
            "ranked_hits": "rescore.ranked",
            "best_poses": "rescore.top_poses",
            "fold_confidence": "fold.confidence",
        },
    }
