"""
Custom exception classes for the Rapier platform.
"""


class RapierError(Exception):
    """Base exception for all Rapier errors."""
    pass


class ValidationError(RapierError):
    """Raised when input or environment validation fails."""
    pass


class ExecutionError(RapierError):
    """Raised when method execution fails."""
    pass


class DependencyError(RapierError):
    """Raised when dependency resolution fails."""
    pass


class ConfigurationError(RapierError):
    """Raised when configuration parsing or validation fails."""
    pass


class SlurmError(RapierError):
    """Raised when SLURM operations fail."""
    pass
