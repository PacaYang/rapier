"""Lightweight mock methods for exercising the workflow builder.

These methods declare realistic typed ports and configuration fields but do no
real science: ``run`` writes small placeholder artifacts for every output
port. They live outside ``rapier.methods`` so ``auto_discover`` never picks
them up; call :func:`register_demo_methods` to opt in.
"""

import json
from typing import Any, Dict, List, Optional

from ..core.method import Method, RunContext
from ..core.specs import ConfigField, PortSpec, ResourceSpec
from ..data import (
    BindingSite, ComplexStructure, File, Molecule, Molecules, Poses, ProteinSequence, ProteinStructure,
    Report, Scores, Structure, Table,
)
from ..methods.registry import MethodRegistry, get_registry


# Placeholder file contents keyed by output format.
_PLACEHOLDERS = {
    "cif": "data_demo\n_entry.id DEMO\n",
    "pdb": "HEADER    DEMO STRUCTURE\nEND\n",
    "sdf": "demo\n  rapier\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n",
    "fasta": ">demo\nMKTAYIAKQR\n",
    "csv": "id,pose,affinity_kcal_mol\ndemo,1,-7.5\n",
    "a3m": ">query\nMKTAYIAKQR\n",
}


class DemoMethod(Method):
    """Base class for declarative mock methods.

    Subclasses declare ``inputs``, ``outputs``, ``config_fields`` and
    ``resources`` like any :class:`~rapier.core.method.Method`; ``run`` writes
    a small placeholder for every output port.
    """

    category = "demo"
    version = "0.1"
    resources = ResourceSpec(cpus=1, memory="2GB", time="00:10:00")

    def run(self, inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        outputs: Dict[str, Any] = {}
        for port_name, port in self.output_spec.ports.items():
            artifact_format = port.formats[0] if port.formats else "json"
            if artifact_format == "box":
                value: Any = port.data_class.inline({"center": [0.0, 0.0, 0.0], "size": [20.0, 20.0, 20.0]}, "box")
            elif artifact_format == "inline":
                value = port.data_class.inline("MKTAYIAKQR", "inline")
            else:
                path = ctx.output_dir / f"{port_name}.{artifact_format}"
                path.write_text(_PLACEHOLDERS.get(
                    artifact_format,
                    json.dumps({"method": self.name, "port": port_name, "config": self.config}, indent=2),
                ))
                value = path
            outputs[port_name] = [value] if port.cardinality == "many" else value
        return outputs


class DemoStructurePrediction(DemoMethod):
    """Predict a protein structure from sequence (mock)."""

    register_name = "demo_structure_prediction"
    display_name = "Structure Prediction"
    category = "prediction"
    inputs = {
        "sequence": PortSpec(ProteinSequence, ("inline", "fasta"), description="Protein sequence to fold"),
        "msa": PortSpec(File, "a3m", required=False, description="Optional precomputed MSA"),
    }
    outputs = {
        "structure": PortSpec(ProteinStructure, "cif", description="Predicted structure"),
        "confidence": PortSpec(Report, "json", description="pLDDT / pTM confidence scores"),
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
        "protein": PortSpec(ProteinSequence, ("inline", "fasta"), description="Protein sequence"),
        "ligand": PortSpec((Molecule, Molecules), ("smiles", "sdf"), description="Ligand(s)"),
    }
    outputs = {
        "complex": PortSpec(ComplexStructure, "cif", description="Predicted protein-ligand complex"),
        "confidence": PortSpec(Report, "json", description="Per-complex confidence"),
        "affinity": PortSpec(Scores, "csv", description="Predicted binding affinity"),
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
        "backbone": PortSpec(Structure, ("pdb", "cif"), description="Backbone to design against"),
    }
    outputs = {
        "sequences": PortSpec(ProteinSequence, "fasta", cardinality="many", description="Designed sequences"),
        "scores": PortSpec(Scores, "csv", description="Per-design log-likelihood"),
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
        "structure": PortSpec(ProteinStructure, "pdb", description="Protein structure (PDB)"),
    }
    outputs = {
        "binding_site": PortSpec(BindingSite, "box", description="Top-ranked pocket box"),
        "pockets": PortSpec(Report, "json", description="All detected pockets"),
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
        "receptor": PortSpec(ProteinStructure, ("pdb", "cif"), description="Receptor structure"),
        "ligands": PortSpec(Molecules, ("sdf", "smiles"), description="Ligand library"),
        "binding_site": PortSpec(BindingSite, "box", required=False, description="Search box; blind docking if unset"),
    }
    outputs = {
        "poses": PortSpec(Poses, "sdf", description="Docked poses"),
        "scores": PortSpec(Scores, "csv", description="Docking scores"),
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
        "poses": PortSpec(Molecules, "sdf", description="Poses to rescore"),
        "receptor": PortSpec(Structure, ("pdb", "cif"), required=False, description="Receptor for interaction scoring"),
        "scores": PortSpec(Table, "csv", required=False, description="Upstream scores to combine"),
    }
    outputs = {
        "ranked": PortSpec(Scores, "csv", description="Ranked candidates"),
        "top_poses": PortSpec(Molecules, "sdf", description="Top-N poses"),
        "summary": PortSpec(Report, "json", description="Run summary"),
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
