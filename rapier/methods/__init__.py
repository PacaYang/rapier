"""Methods module - screening algorithm implementations.

Every method is a :class:`rapier.core.method.Method` subclass with typed
input/output ports over the data types in :mod:`rapier.data`. This package
provides the concrete docking, filtering, preparation, prediction and
similarity methods, the registry used to discover them, and the generic
scheduler runner (:mod:`rapier.methods.runner`).
"""

from ..core.method import Method, MethodError, RunContext
from .base_implementations import ParallelizableMethod, CheckpointableMethod
from .registry import MethodRegistry, get_registry
from .utils import (
    parse_box_spec,
    check_executable,
    check_executables,
    validate_pdb_file,
    validate_sdf_file,
    parse_vina_output,
)

# Import concrete method implementations
from .screening import LigandFilter, MoleculeFilter, SimilaritySearch
from .preparation import LigandPreparation, ProteinPreparation
from .docking import VinaDocking, SminaDocking
from .prediction import Boltz2, Boltz2Prediction, Boltz2PredictionMethod


__all__ = [
    # Base class and mixins
    'Method',
    'MethodError',
    'RunContext',
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
    'MoleculeFilter',
    'SimilaritySearch',
    # Preparation methods
    'LigandPreparation',
    'ProteinPreparation',
    # Docking methods
    'VinaDocking',
    'SminaDocking',
    # Prediction methods
    'Boltz2Prediction',
    'Boltz2',
    'Boltz2PredictionMethod',
]
