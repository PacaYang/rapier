"""
Specification classes for defining inputs, outputs, environments, and resources.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from rapier.artifacts.contracts import (
    ConfigField,
    ConfigSpec,
    InputSpec,
    OutputSpec,
    PortSpec,
)


@dataclass
class EnvironmentSpec:
    """Specification for execution environment requirements."""

    executables: List[str] = field(default_factory=list)
    """Required executables (e.g., ['vina', 'obabel'])"""

    modules: List[str] = field(default_factory=list)
    """SLURM modules to load (e.g., ['vina/1.2.0', 'python/3.9'])"""

    conda_env: Optional[str] = None
    """Conda environment name"""

    python_packages: List[str] = field(default_factory=list)
    """Required Python packages (e.g., ['rdkit', 'numpy'])"""

    env_variables: Dict[str, str] = field(default_factory=dict)
    """Environment variables to set"""

    def validate(self) -> tuple[bool, List[str]]:
        """
        Validate that environment requirements are satisfied.

        Returns:
            Tuple of (is_valid, error_messages)
        """
        import shutil
        errors = []

        # Check executables
        for executable in self.executables:
            if not shutil.which(executable):
                errors.append(f"Required executable not found: {executable}")

        # Check Python packages
        for package in self.python_packages:
            try:
                __import__(package)
            except ImportError:
                errors.append(f"Required Python package not found: {package}")

        return len(errors) == 0, errors


@dataclass
class ResourceSpec:
    """Specification for SLURM resource requirements."""

    cpus: int = 1
    """Number of CPUs"""

    memory: str = "4GB"
    """Memory requirement (e.g., '4GB', '8000MB')"""

    gpus: int = 0
    """Number of GPUs"""

    gpu_type: Optional[str] = None
    """GPU type (e.g., 'V100', 'A100')"""

    time: str = "1:00:00"
    """Time limit (HH:MM:SS format)"""

    partition: Optional[str] = None
    """SLURM partition name"""

    nodes: int = 1
    """Number of nodes"""

    def to_slurm_directives(self) -> List[str]:
        """
        Convert resource spec to SLURM directives.

        Returns:
            List of SLURM directive strings
        """
        directives = [
            f"#SBATCH --cpus-per-task={self.cpus}",
            f"#SBATCH --mem={self.memory}",
            f"#SBATCH --time={self.time}",
            f"#SBATCH --nodes={self.nodes}",
        ]

        if self.gpus > 0:
            if self.gpu_type:
                directives.append(f"#SBATCH --gres=gpu:{self.gpu_type}:{self.gpus}")
            else:
                directives.append(f"#SBATCH --gres=gpu:{self.gpus}")

        if self.partition:
            directives.append(f"#SBATCH --partition={self.partition}")

        return directives
