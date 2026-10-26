"""
Method configuration dataclass.

Represents a single method in a pipeline configuration.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import warnings

from rapier.core.resources import ResourceController


@dataclass
class MethodConfig:
    """
    Configuration for a single method in a pipeline.

    Attributes:
        id: Unique identifier for the method
        type: Method type (e.g., 'vina_dock', 'filtering')
        description: Optional description
        parameters: Method-specific parameters
        resources: Resource requirements (CPUs, memory, GPUs, etc.)
        resource_controller: Single resource request, replacing legacy resources
        failure_policy: How to handle failures ('stop', 'continue', 'retry')
        retry_count: Number of retries if failure_policy is 'retry'
    """

    id: str
    type: str
    description: Optional[str] = None
    inputs: Dict[str, str] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    parameters: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None
    failure_policy: str = "stop"
    retry_count: int = 1
    resource_controller: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validate method configuration after initialization."""
        if not self.id:
            raise ValueError("Method id cannot be empty")
        if not self.type:
            raise ValueError("Method type cannot be empty")
        if self.failure_policy not in ("stop", "continue", "retry"):
            raise ValueError(f"Invalid failure_policy: {self.failure_policy}")
        if self.retry_count < 1:
            raise ValueError("retry_count must be at least 1")
        if self.parameters is not None and self.config:
            raise ValueError(
                f"Method '{self.id}' cannot define both 'config' and legacy 'parameters'"
            )
        if self.parameters is not None:
            warnings.warn(
                f"Method '{self.id}': 'parameters' is deprecated; use 'config'",
                DeprecationWarning,
                stacklevel=2,
            )
            self.config = dict(self.parameters)
        # Keep the public v1 attribute as a read-compatible alias.
        self.parameters = self.config

        if self.resource_controller is not None and self.resources is not None:
            raise ValueError(
                f"Method '{self.id}' cannot define both 'resource_controller' "
                "and legacy 'resources'"
            )
        resource_request = (
            self.resource_controller
            if self.resource_controller is not None
            else self.resources
        )
        self.resource_controller = ResourceController(resource_request).to_dict()
        # Existing callers can continue reading the normalized request here.
        self.resources = self.resource_controller

        for port_name, reference in self.inputs.items():
            if not isinstance(port_name, str) or not port_name:
                raise ValueError("Method input port names must be non-empty strings")
            if not isinstance(reference, str) or not reference:
                raise ValueError(
                    f"Input reference for port '{port_name}' must be a string"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "inputs": self.inputs,
            "config": self.config,
            "resource_controller": dict(self.resource_controller),
            "failure_policy": self.failure_policy,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MethodConfig":
        """
        Create MethodConfig from dictionary.

        Args:
            data: Dictionary containing method configuration

        Returns:
            MethodConfig instance
        """
        return cls(
            id=data["id"],
            type=data["type"],
            description=data.get("description"),
            inputs=data.get("inputs", {}),
            config=data.get("config", {}),
            parameters=data.get("parameters") if "parameters" in data else None,
            resources=data.get("resources"),
            resource_controller=data.get("resource_controller"),
            failure_policy=data.get("failure_policy", "stop"),
            retry_count=data.get("retry_count", 1),
        )


@dataclass
class ConnectionConfig:
    """
    Configuration for a connection between methods.

    Attributes:
        from_methods: Source method ID(s) - single string or list
        to_method: Target method ID
        connection_type: 'serial' or 'parallel'
    """

    from_methods: Any  # str or list of str
    to_method: str
    connection_type: str = "serial"

    def __post_init__(self):
        """Validate connection configuration."""
        if isinstance(self.from_methods, str):
            self.from_methods = [self.from_methods]
        elif not isinstance(self.from_methods, list):
            raise ValueError("from_methods must be a string or list of strings")

        if not self.to_method:
            raise ValueError("to_method cannot be empty")

        if self.connection_type not in ("serial", "parallel"):
            raise ValueError(f"Invalid connection_type: {self.connection_type}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "from": (
                self.from_methods
                if len(self.from_methods) > 1
                else self.from_methods[0]
            ),
            "to": self.to_method,
            "type": self.connection_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConnectionConfig":
        """
        Create ConnectionConfig from dictionary.

        Args:
            data: Dictionary containing connection configuration

        Returns:
            ConnectionConfig instance
        """
        return cls(
            from_methods=data["from"],
            to_method=data["to"],
            connection_type=data.get("type", "serial"),
        )


@dataclass
class PoolingRuleConfig:
    """
    Configuration for result pooling from parallel branches.

    Attributes:
        strategy: Pooling strategy ('any', 'all', 'majority', 'best', 'first', 'custom')
        metric: Metric name for 'best' strategy
        minimize: Whether to minimize the metric (default: maximize)
        function: Custom pooling function name for 'custom' strategy
    """

    strategy: str
    metric: Optional[str] = None
    minimize: bool = False
    function: Optional[str] = None

    def __post_init__(self):
        """Validate pooling rule configuration."""
        valid_strategies = {"any", "all", "majority", "best", "first", "custom"}
        if self.strategy not in valid_strategies:
            raise ValueError(f"Invalid pooling strategy: {self.strategy}")

        if self.strategy == "best" and not self.metric:
            raise ValueError("'best' strategy requires a metric")

        if self.strategy == "custom" and not self.function:
            raise ValueError("'custom' strategy requires a function")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {"strategy": self.strategy}
        if self.metric:
            result["metric"] = self.metric
        if self.minimize:
            result["minimize"] = self.minimize
        if self.function:
            result["function"] = self.function
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PoolingRuleConfig":
        """
        Create PoolingRuleConfig from dictionary.

        Args:
            data: Dictionary containing pooling rule configuration

        Returns:
            PoolingRuleConfig instance
        """
        return cls(
            strategy=data["strategy"],
            metric=data.get("metric"),
            minimize=data.get("minimize", False),
            function=data.get("function"),
        )
