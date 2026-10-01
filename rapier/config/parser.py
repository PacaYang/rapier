"""
Configuration parser for pipeline YAML/JSON files.

Handles parsing, variable substitution, and includes.
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from .pipeline_config import PipelineConfig


class ConfigParser:
    """
    Parser for pipeline configuration files.

    Supports YAML and JSON formats, variable substitution,
    and configuration includes.
    """

    def __init__(self, project_dir: Optional[str] = None):
        """
        Initialize the parser.

        Args:
            project_dir: Base directory for resolving relative paths
        """
        self.project_dir = project_dir or os.getcwd()
        self.variables: Dict[str, str] = {}

    def parse_file(self, file_path: str) -> PipelineConfig:
        """
        Parse a configuration file.

        Args:
            file_path: Path to YAML or JSON configuration file

        Returns:
            PipelineConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is invalid
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        # Update project_dir based on config file location
        if self.project_dir == os.getcwd():
            self.project_dir = str(path.parent.absolute())

        with open(path, 'r') as f:
            content = f.read()

        return self.parse_string(content)

    def parse_string(self, content: str) -> PipelineConfig:
        """
        Parse configuration from a string.

        Args:
            content: YAML or JSON string

        Returns:
            PipelineConfig instance

        Raises:
            ValueError: If content is invalid
        """
        # Substitute variables
        content = self._substitute_variables(content)

        # Parse YAML (also handles JSON as it's a subset)
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse configuration: {e}")

        if not isinstance(data, dict):
            raise ValueError("Configuration must be a dictionary")

        return self.parse_dict(data)

    def parse_dict(self, data: Dict[str, Any]) -> PipelineConfig:
        """
        Parse configuration from a dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            PipelineConfig instance
        """
        # Store environment variables if present
        if 'environment' in data and 'variables' in data['environment']:
            self.variables.update(data['environment']['variables'])

        # Add project_dir to environment
        if 'environment' not in data:
            data['environment'] = {}
        data['environment']['project_dir'] = self.project_dir

        # Process includes if present
        if 'include' in data:
            data = self._process_includes(data)

        return PipelineConfig.from_dict(data)

    def _substitute_variables(self, content: str) -> str:
        """
        Substitute variables in the configuration content.

        Supports:
        - Environment variables: ${VAR_NAME}
        - Project-relative paths: ${PROJECT_DIR}/path

        Args:
            content: Configuration string with variables

        Returns:
            Content with substituted variables
        """
        # Pattern to match ${VAR_NAME}
        pattern = r'\$\{([^}]+)\}'

        def replace_var(match):
            var_name = match.group(1)

            # Check if it's PROJECT_DIR
            if var_name == 'PROJECT_DIR':
                return self.project_dir

            # Check custom variables
            if var_name in self.variables:
                return self.variables[var_name]

            # Check environment variables
            if var_name in os.environ:
                return os.environ[var_name]

            # Leave unchanged if not found
            return match.group(0)

        return re.sub(pattern, replace_var, content)

    def _process_includes(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process included configuration files.

        Args:
            data: Configuration dictionary with 'include' field

        Returns:
            Merged configuration dictionary
        """
        includes = data.pop('include', [])
        if isinstance(includes, str):
            includes = [includes]

        # Load and merge included configurations
        for include_path in includes:
            # Resolve relative to project_dir
            full_path = Path(self.project_dir) / include_path
            if not full_path.exists():
                raise FileNotFoundError(f"Included file not found: {include_path}")

            with open(full_path, 'r') as f:
                included_data = yaml.safe_load(f)

            # Merge included data (current data takes precedence)
            data = self._merge_configs(included_data, data)

        return data

    def _merge_configs(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge two configuration dictionaries.

        Args:
            base: Base configuration
            override: Overriding configuration

        Returns:
            Merged configuration
        """
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                # Append lists
                result[key] = result[key] + value
            else:
                result[key] = value

        return result

    def set_variable(self, name: str, value: str):
        """
        Set a custom variable for substitution.

        Args:
            name: Variable name
            value: Variable value
        """
        self.variables[name] = value

    def set_project_dir(self, project_dir: str):
        """
        Set the project directory for path resolution.

        Args:
            project_dir: Project directory path
        """
        self.project_dir = project_dir
