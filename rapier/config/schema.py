"""
YAML/JSON schema definitions for pipeline configuration.

This module defines the structure and validation rules for pipeline
configuration files. The schema supports both JSON Schema validation
and custom validation logic.
"""

from typing import Dict, Any

# JSON Schema for pipeline configuration
PIPELINE_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name", "methods"],
    "properties": {
        "name": {
            "type": "string",
            "description": "Pipeline name",
            "minLength": 1
        },
        "description": {
            "type": "string",
            "description": "Pipeline description"
        },
        "version": {
            "type": "string",
            "description": "Configuration version",
            "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"
        },
        "methods": {
            "type": "array",
            "description": "List of methods in the pipeline",
            "minItems": 1,
            "items": {"$ref": "#/definitions/method"}
        },
        "inputs": {
            "type": "object",
            "description": "Reusable normalized pipeline artifacts",
            "additionalProperties": {"$ref": "#/definitions/artifact"}
        },
        "outputs": {
            "type": "object",
            "description": "Exported method output references",
            "additionalProperties": {"type": "string"}
        },
        "connections": {
            "type": "array",
            "description": "Connections between methods",
            "items": {"$ref": "#/definitions/connection"}
        },
        "pooling_rules": {
            "type": "object",
            "description": "Rules for pooling results from parallel branches",
            "additionalProperties": {"$ref": "#/definitions/pooling_rule"}
        },
        "failure_policy": {
            "type": "string",
            "description": "Global failure handling policy",
            "enum": ["stop", "continue", "retry"]
        },
        "environment": {
            "type": "object",
            "description": "Global environment settings",
            "properties": {
                "variables": {
                    "type": "object",
                    "additionalProperties": {"type": "string"}
                },
                "project_dir": {
                    "type": "string"
                }
            }
        }
    },
    "definitions": {
        "method": {
            "type": "object",
            "required": ["id", "type"],
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Unique method identifier",
                    "pattern": "^[a-zA-Z][a-zA-Z0-9_-]*$"
                },
                "type": {
                    "type": "string",
                    "description": "Method type (e.g., 'vina_dock', 'filtering')"
                },
                "description": {
                    "type": "string",
                    "description": "Method description"
                },
                "parameters": {
                    "type": "object",
                    "description": "Deprecated alias for method config",
                    "additionalProperties": True
                },
                "config": {
                    "type": "object",
                    "description": "Strict method-specific configuration",
                    "additionalProperties": True
                },
                "inputs": {
                    "type": "object",
                    "description": "Named artifact port references",
                    "additionalProperties": {"type": "string"}
                },
                "resources": {
                    "$ref": "#/definitions/resources"
                },
                "failure_policy": {
                    "type": "string",
                    "enum": ["stop", "continue", "retry"],
                    "description": "Failure policy for this method"
                },
                "retry_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Number of retries if failure_policy is 'retry'"
                }
            }
        },
        "artifact": {
            "type": "object",
            "required": ["type", "format"],
            "properties": {
                "type": {"type": "string", "minLength": 1},
                "format": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "value": {},
                "metadata": {"type": "object"}
            },
            "oneOf": [
                {"required": ["source"], "not": {"required": ["value"]}},
                {"required": ["value"], "not": {"required": ["source"]}}
            ]
        },
        "connection": {
            "type": "object",
            "required": ["from", "to"],
            "properties": {
                "from": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}}
                    ],
                    "description": "Source method ID(s)"
                },
                "to": {
                    "type": "string",
                    "description": "Target method ID"
                },
                "type": {
                    "type": "string",
                    "enum": ["serial", "parallel"],
                    "default": "serial",
                    "description": "Connection type"
                }
            }
        },
        "pooling_rule": {
            "type": "object",
            "required": ["strategy"],
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": "Pooling strategy: 'any', 'all', 'majority', 'best', 'first', 'custom'"
                },
                "metric": {
                    "type": "string",
                    "description": "Metric to use for 'best' strategy"
                },
                "minimize": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to minimize the metric (default: maximize)"
                },
                "function": {
                    "type": "string",
                    "description": "Custom pooling function for 'custom' strategy"
                }
            }
        },
        "resources": {
            "type": "object",
            "properties": {
                "cpus": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of CPUs"
                },
                "memory": {
                    "type": "string",
                    "pattern": "^[0-9]+(MB|GB|TB)$",
                    "description": "Memory requirement (e.g., '4GB')"
                },
                "gpus": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Number of GPUs"
                },
                "time_limit": {
                    "type": "string",
                    "pattern": "^[0-9]+(s|m|h|d)$",
                    "description": "Time limit (e.g., '1h', '30m')"
                },
                "partition": {
                    "type": "string",
                    "description": "SLURM partition name"
                }
            }
        }
    }
}


def validate_pooling_strategy(strategy: str, metric: str = None) -> bool:
    """
    Validate pooling strategy syntax.

    Args:
        strategy: Pooling strategy string
        metric: Optional metric name for 'best' strategy

    Returns:
        True if valid, False otherwise
    """
    valid_strategies = {'any', 'all', 'majority', 'best', 'first', 'custom'}

    if strategy not in valid_strategies:
        return False

    if strategy == 'best' and not metric:
        return False

    return True


def validate_failure_policy(policy: str) -> bool:
    """
    Validate failure policy string.

    Args:
        policy: Failure policy string

    Returns:
        True if valid, False otherwise
    """
    valid_policies = {'stop', 'continue', 'retry'}
    return policy in valid_policies


def validate_resource_spec(resources: Dict[str, Any]) -> bool:
    """
    Validate resource specification.

    Args:
        resources: Resource specification dictionary

    Returns:
        True if valid, False otherwise
    """
    if not resources:
        return True

    # Validate memory format
    if 'memory' in resources:
        memory = resources['memory']
        if not isinstance(memory, str):
            return False
        if not any(memory.endswith(unit) for unit in ['MB', 'GB', 'TB']):
            return False

    # Validate time limit format
    if 'time_limit' in resources:
        time_limit = resources['time_limit']
        if not isinstance(time_limit, str):
            return False
        if not any(time_limit.endswith(unit) for unit in ['s', 'm', 'h', 'd']):
            return False

    return True
