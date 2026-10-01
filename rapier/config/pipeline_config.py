"""
Pipeline configuration dataclass.

Represents a complete pipeline configuration, parsed and validated.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from .method_config import MethodConfig, ConnectionConfig, PoolingRuleConfig
from rapier.artifacts import Artifact, ArtifactStorage


_UNSET = object()


@dataclass
class ArtifactConfig:
    """A reusable typed artifact declared at the pipeline boundary."""

    type: str
    format: str
    source: Optional[str] = None
    value: Any = _UNSET
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        has_source = self.source is not None
        has_value = self.value is not _UNSET
        if has_source == has_value:
            raise ValueError("Artifact must define exactly one of 'source' or 'value'")

    def to_artifact(self) -> Artifact:
        if self.source is not None:
            return Artifact(
                type=self.type,
                format=self.format,
                storage=ArtifactStorage.FILE,
                value=self.source,
                metadata=dict(self.metadata),
            )
        return Artifact(
            type=self.type,
            format=self.format,
            storage=ArtifactStorage.INLINE,
            value=self.value,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            'type': self.type,
            'format': self.format,
            'metadata': self.metadata,
        }
        if self.source is not None:
            result['source'] = self.source
        else:
            result['value'] = self.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any], project_dir: Optional[str] = None) -> 'ArtifactConfig':
        source = data.get('source')
        if source is not None and project_dir:
            from pathlib import Path
            source_path = Path(source)
            if not source_path.is_absolute():
                source = str(Path(project_dir) / source_path)
        kwargs = {
            'type': data['type'],
            'format': data['format'],
            'source': source,
            'metadata': data.get('metadata', {}),
        }
        if 'value' in data:
            kwargs['value'] = data['value']
        return cls(**kwargs)


@dataclass
class PipelineConfig:
    """
    Complete pipeline configuration.

    Attributes:
        name: Pipeline name
        description: Optional pipeline description
        version: Configuration version
        methods: List of method configurations
        connections: List of connections between methods
        pooling_rules: Dictionary mapping method IDs to pooling rules
        failure_policy: Global failure handling policy
        environment: Environment settings (variables, project_dir)
    """

    name: str
    methods: List[MethodConfig]
    description: Optional[str] = None
    version: Optional[str] = None
    connections: List[ConnectionConfig] = field(default_factory=list)
    pooling_rules: Dict[str, PoolingRuleConfig] = field(default_factory=dict)
    failure_policy: str = "stop"
    environment: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, ArtifactConfig] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """Validate pipeline configuration after initialization."""
        if not self.name:
            raise ValueError("Pipeline name cannot be empty")
        if not self.methods:
            raise ValueError("Pipeline must have at least one method")
        if self.failure_policy not in ('stop', 'continue', 'retry'):
            raise ValueError(f"Invalid failure_policy: {self.failure_policy}")
        for name, reference in self.outputs.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Pipeline output names must be non-empty strings")
            if not isinstance(reference, str) or not reference:
                raise ValueError(f"Pipeline output reference '{name}' must be a string")

    def get_method(self, method_id: str) -> Optional[MethodConfig]:
        """
        Get method configuration by ID.

        Args:
            method_id: Method identifier

        Returns:
            MethodConfig if found, None otherwise
        """
        for method in self.methods:
            if method.id == method_id:
                return method
        return None

    def get_method_ids(self) -> List[str]:
        """Get list of all method IDs in the pipeline."""
        return [method.id for method in self.methods]

    def has_method(self, method_id: str) -> bool:
        """Check if a method with given ID exists."""
        return self.get_method(method_id) is not None

    def get_upstream_methods(self, method_id: str) -> List[str]:
        """
        Get list of method IDs that connect to the given method.

        Args:
            method_id: Target method ID

        Returns:
            List of upstream method IDs
        """
        upstream = []
        for conn in self.connections:
            if conn.to_method == method_id:
                upstream.extend(conn.from_methods)
        method = self.get_method(method_id)
        if method:
            for reference in method.inputs.values():
                if reference.startswith('pipeline.'):
                    continue
                source, separator, _ = reference.partition('.')
                if separator and source not in upstream:
                    upstream.append(source)
        return upstream

    def get_downstream_methods(self, method_id: str) -> List[str]:
        """
        Get list of method IDs that the given method connects to.

        Args:
            method_id: Source method ID

        Returns:
            List of downstream method IDs
        """
        downstream = []
        for conn in self.connections:
            if method_id in conn.from_methods:
                downstream.append(conn.to_method)
        return downstream

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            'name': self.name,
            'methods': [m.to_dict() for m in self.methods],
        }

        if self.inputs:
            result['inputs'] = {name: value.to_dict() for name, value in self.inputs.items()}
        if self.outputs:
            result['outputs'] = dict(self.outputs)

        if self.description:
            result['description'] = self.description
        if self.version:
            result['version'] = self.version
        if self.connections:
            result['connections'] = [c.to_dict() for c in self.connections]
        if self.pooling_rules:
            result['pooling_rules'] = {k: v.to_dict() for k, v in self.pooling_rules.items()}
        if self.failure_policy != 'stop':
            result['failure_policy'] = self.failure_policy
        if self.environment:
            result['environment'] = self.environment

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PipelineConfig':
        """
        Create PipelineConfig from dictionary.

        Args:
            data: Dictionary containing pipeline configuration

        Returns:
            PipelineConfig instance
        """
        methods = [MethodConfig.from_dict(m) for m in data['methods']]

        project_dir = data.get('environment', {}).get('project_dir')
        inputs = {
            name: ArtifactConfig.from_dict(value, project_dir=project_dir)
            for name, value in data.get('inputs', {}).items()
        }

        connections = []
        if 'connections' in data:
            connections = [ConnectionConfig.from_dict(c) for c in data['connections']]

        pooling_rules = {}
        if 'pooling_rules' in data:
            pooling_rules = {
                k: PoolingRuleConfig.from_dict(v)
                for k, v in data['pooling_rules'].items()
            }

        return cls(
            name=data['name'],
            description=data.get('description'),
            version=data.get('version'),
            methods=methods,
            connections=connections,
            pooling_rules=pooling_rules,
            failure_policy=data.get('failure_policy', 'stop'),
            environment=data.get('environment', {}),
            inputs=inputs,
            outputs=data.get('outputs', {}),
        )

    def to_pipeline(self):
        """
        Convert this configuration to a Pipeline object.

        Returns:
            Pipeline instance

        Note:
            This method will be implemented after integration with
            the Pipeline class from rapier.core.pipeline
        """
        from rapier.core.pipeline import Pipeline
        return Pipeline.from_config(self)
