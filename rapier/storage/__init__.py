"""File management and project structure for the Rapier platform."""

from .structure import ProjectStructure
from .path_manager import PathManager
from .metadata import FileMetadata
from .file_tracker import FileTracker
from .file_organizer import FileOrganizer
from rapier.core.specs import InputSpec, OutputSpec
from .cleanup_manager import CleanupManager, CleanupPolicy, POLICIES
from .workspace import Workspace
from . import validators

__all__ = [
    # Core classes
    'ProjectStructure',
    'PathManager',
    'FileMetadata',
    'FileTracker',
    'FileOrganizer',
    'CleanupManager',
    'Workspace',

    # Specs and policies
    'InputSpec',
    'OutputSpec',
    'CleanupPolicy',
    'POLICIES',

    # Validators module
    'validators',
]
