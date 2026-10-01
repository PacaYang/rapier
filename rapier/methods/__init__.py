"""Methods module - screening algorithm implementations.

This module provides base classes, utilities, and concrete implementations
of virtual screening methods including docking, filtering, and similarity search.
"""

from .base_implementations import (
    SingleStepMethod,
    MultiStepMethod,
    MethodStep,
    ParallelizableMethod,
    CheckpointableMethod
)
from .registry import MethodRegistry, get_registry
from .utils import (
    parse_box_spec,
    check_executable,
    check_executables,
    validate_pdb_file,
    validate_sdf_file,
    parse_vina_output
)

# Import concrete method implementations
from .screening import LigandFilter, SimilaritySearch
from .preparation import LigandPreparation, ProteinPreparation
from .docking import VinaDocking, SminaDocking
from .prediction import Boltz2, Boltz2Prediction, Boltz2PredictionMethod


__all__ = [
    # Base classes
    'SingleStepMethod',
    'MultiStepMethod',
    'MethodStep',
    'ParallelizableMethod',
    'CheckpointableMethod',
    # Registry
    'MethodRegistry',
    'get_registry',
    # Utilities
    'parse_box_spec',
    'check_executable',
    'check_executables',
    'validate_pdb_file',
    'validate_sdf_file',
    'parse_vina_output',
    # Screening methods
    'LigandFilter',
    'SimilaritySearch',
    # Preparation methods
    'LigandPreparation',
    'ProteinPreparation',
    # Docking methods
    'VinaDocking',
    'SminaDocking',
    'Boltz2Prediction',
    'Boltz2',
    'Boltz2PredictionMethod',
]
