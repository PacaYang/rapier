"""
Pipeline configuration module.

Provides tools for parsing, validating, and managing pipeline configurations
from YAML/JSON files.
"""

from .pipeline_config import ArtifactConfig, PipelineConfig
from .workflow_metadata import WorkflowMetadata
from .method_config import MethodConfig, ConnectionConfig, PoolingRuleConfig
from .parser import ConfigParser
from .validator import ConfigValidator
from .templates import (
    create_simple_pipeline,
    create_parallel_pipeline,
    create_pooling_pipeline,
    create_diamond_pipeline,
)

__all__ = [
    "PipelineConfig",
    "ArtifactConfig",
    "WorkflowMetadata",
    "MethodConfig",
    "ConnectionConfig",
    "PoolingRuleConfig",
    "ConfigParser",
    "ConfigValidator",
    "create_simple_pipeline",
    "create_parallel_pipeline",
    "create_pooling_pipeline",
    "create_diamond_pipeline",
]
