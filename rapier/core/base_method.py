"""
Base class for all method implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path

from rapier.core.specs import (
    ConfigSpec,
    InputSpec,
    OutputSpec,
    EnvironmentSpec,
    ResourceSpec,
)
from rapier.core.enums import MethodType, TaskStatus
from rapier.core.exceptions import ValidationError, ExecutionError


class BaseMethod(ABC):
    """
    Abstract base class for all screening methods.

    Methods must implement the lifecycle hooks:
    - validate_inputs: Check that inputs are valid
    - validate_environment: Check that required software is available
    - execute: Run the method
    """

    def __init__(
        self,
        name: str,
        version: str,
        parameters: Optional[Dict[str, Any]] = None,
        method_type: MethodType = MethodType.SINGLE_STEP,
    ):
        """
        Initialize method.

        Args:
            name: Method name
            version: Method version
            parameters: Method parameters
            method_type: Single-step or multi-step
        """
        self.name = name
        self.version = version
        self.parameters = parameters or {}
        self.config = dict(self.parameters)
        self.method_type = method_type
        self._input_spec: Optional[InputSpec] = None
        self._output_spec: Optional[OutputSpec] = None
        self._env_spec: Optional[EnvironmentSpec] = None
        self._resource_spec: Optional[ResourceSpec] = None
        self._config_spec: Optional[ConfigSpec] = None

    @property
    def config_spec(self) -> ConfigSpec:
        """Get the method-specific configuration schema."""
        if self._config_spec is None:
            self._config_spec = self._create_config_spec()
        return self._config_spec

    @config_spec.setter
    def config_spec(self, value: ConfigSpec):
        self._config_spec = value

    @property
    def input_spec(self) -> InputSpec:
        """Get input specification."""
        if self._input_spec is None:
            self._input_spec = self._create_input_spec()
        return self._input_spec

    @input_spec.setter
    def input_spec(self, value: InputSpec):
        """Set input specification directly."""
        self._input_spec = value

    @property
    def output_spec(self) -> OutputSpec:
        """Get output specification."""
        if self._output_spec is None:
            self._output_spec = self._create_output_spec()
        return self._output_spec

    @output_spec.setter
    def output_spec(self, value: OutputSpec):
        """Set output specification directly."""
        self._output_spec = value

    @property
    def env_requirements(self) -> EnvironmentSpec:
        """Get environment requirements."""
        if self._env_spec is None:
            self._env_spec = self._create_env_spec()
        return self._env_spec

    @env_requirements.setter
    def env_requirements(self, value: EnvironmentSpec):
        """Set environment requirements directly."""
        self._env_spec = value

    @property
    def resource_spec(self) -> ResourceSpec:
        """Get resource requirements."""
        if self._resource_spec is None:
            self._resource_spec = self._create_resource_spec()
        return self._resource_spec

    @resource_spec.setter
    def resource_spec(self, value: ResourceSpec):
        """Set resource specification directly."""
        self._resource_spec = value

    def _create_input_spec(self) -> InputSpec:
        """Create input specification for this method.

        Override this method OR set input_spec directly in __init__.
        """
        return InputSpec()

    def _create_config_spec(self) -> ConfigSpec:
        """Create the config schema; v1 methods remain permissive."""
        return ConfigSpec(strict=False)

    def normalize_config(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate settings and store their normalized/defaulted values."""
        self.config = self.config_spec.normalize(self.parameters if config is None else config)
        self.parameters = self.config
        return self.config

    def _create_output_spec(self) -> OutputSpec:
        """Create output specification for this method.

        Override this method OR set output_spec directly in __init__.
        """
        return OutputSpec()

    def _create_env_spec(self) -> EnvironmentSpec:
        """Create environment specification for this method.

        Override this method OR set env_requirements directly in __init__.
        """
        return EnvironmentSpec()

    def _create_resource_spec(self) -> ResourceSpec:
        """Create resource specification for this method. Override if needed."""
        return ResourceSpec()

    @abstractmethod
    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, List[str]]:
        """
        Validate method inputs.

        Args:
            inputs: Dictionary of input values

        Returns:
            Tuple of (is_valid, error_messages)
        """
        pass

    @abstractmethod
    def validate_environment(self) -> tuple[bool, List[str]]:
        """
        Validate that execution environment meets requirements.

        Returns:
            Tuple of (is_valid, error_messages)
        """
        pass

    def prepare(self, task: 'Task') -> None:
        """
        Prepare for execution (optional hook).

        Args:
            task: Task instance
        """
        pass

    @abstractmethod
    def execute(self, task: 'Task') -> TaskStatus:
        """
        Execute the method.

        Args:
            task: Task instance with inputs and working directory

        Returns:
            Task status after execution
        """
        pass

    def collect_results(self, task: 'Task') -> Dict[str, Any]:
        """
        Collect and parse results after execution (optional hook).

        Args:
            task: Task instance

        Returns:
            Dictionary of collected results
        """
        return {}

    def collect_outputs(self, task: 'Task') -> Dict[str, Any]:
        """
        Collect outputs from completed task.

        Alias for collect_results for compatibility with task runner.

        Args:
            task: Task instance

        Returns:
            Dictionary of outputs
        """
        return self.collect_results(task)

    def get_command(self, task: 'Task') -> str:
        """
        Generate command to execute for this task.

        Default implementation returns a placeholder.
        Override in subclasses to provide actual command.

        Args:
            task: Task instance

        Returns:
            Command string to execute
        """
        return f"echo 'Executing {self.name} for task {task.task_id}'"

    def get_slurm_script(self, task: 'Task') -> str:
        """
        Generate SLURM job script for this method.

        Args:
            task: Task instance

        Returns:
            SLURM script content
        """
        # Will be implemented with SLURM integration in Stage 3
        raise NotImplementedError("SLURM integration not yet implemented")

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'BaseMethod':
        """
        Create method instance from configuration.

        Args:
            config: Method configuration dictionary

        Returns:
            Method instance
        """
        return cls(
            name=config.get('name', cls.__name__),
            version=config.get('version', '1.0'),
            parameters=config.get('parameters', {}),
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, version={self.version})"
    contract_version = 1
