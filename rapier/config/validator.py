"""
Configuration validator for pipeline configurations.

Validates structure, connections, method references, and circular dependencies.
"""

from typing import List, Tuple, Set, Dict, Any
from .pipeline_config import PipelineConfig
from .method_config import MethodConfig
from .schema import validate_pooling_strategy, validate_failure_policy, validate_resource_spec
from rapier.artifacts import get_artifact_registry, get_converter_registry


class ConfigValidator:
    """
    Validator for pipeline configurations.

    Performs structural validation, dependency checks,
    and circular dependency detection.
    """

    def validate(self, config: PipelineConfig) -> Tuple[bool, List[str]]:
        """
        Validate a complete pipeline configuration.

        Args:
            config: PipelineConfig to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Validate structure
        struct_errors = self.validate_structure(config)
        errors.extend(struct_errors)

        # Validate connections
        conn_errors = self.validate_connections(config)
        errors.extend(conn_errors)

        # Validate methods
        method_errors = self.validate_methods(config)
        errors.extend(method_errors)

        # Check for circular dependencies
        circular_errors = self._check_circular_dependencies(config)
        errors.extend(circular_errors)

        # Validate pooling rules
        pooling_errors = self._validate_pooling_rules(config)
        errors.extend(pooling_errors)

        # Validate normalized artifacts, ports, and method configs.
        errors.extend(self.validate_contracts(config))

        return (len(errors) == 0, errors)

    def validate_structure(self, config: PipelineConfig) -> List[str]:
        """
        Validate the basic structure of the configuration.

        Args:
            config: PipelineConfig to validate

        Returns:
            List of validation errors
        """
        errors = []

        # Check name
        if not config.name or not config.name.strip():
            errors.append("Pipeline name cannot be empty")

        # Check methods
        if not config.methods:
            errors.append("Pipeline must have at least one method")

        # Check for duplicate method IDs
        method_ids = [m.id for m in config.methods]
        duplicates = [mid for mid in method_ids if method_ids.count(mid) > 1]
        if duplicates:
            errors.append(f"Duplicate method IDs found: {set(duplicates)}")

        # Validate failure policy
        if not validate_failure_policy(config.failure_policy):
            errors.append(f"Invalid global failure policy: {config.failure_policy}")

        return errors

    def validate_connections(self, config: PipelineConfig) -> List[str]:
        """
        Validate connections between methods.

        Args:
            config: PipelineConfig to validate

        Returns:
            List of validation errors
        """
        errors = []
        method_ids = set(config.get_method_ids())

        for i, conn in enumerate(config.connections):
            # Check that source methods exist
            for from_id in conn.from_methods:
                if from_id not in method_ids:
                    errors.append(
                        f"Connection {i}: source method '{from_id}' does not exist"
                    )

            # Check that target method exists
            if conn.to_method not in method_ids:
                errors.append(
                    f"Connection {i}: target method '{conn.to_method}' does not exist"
                )

        return errors

    def validate_methods(self, config: PipelineConfig) -> List[str]:
        """
        Validate individual method configurations.

        Args:
            config: PipelineConfig to validate

        Returns:
            List of validation errors
        """
        errors = []

        for method in config.methods:
            # Validate method ID format
            if not method.id or not method.id[0].isalpha():
                errors.append(
                    f"Method '{method.id}': ID must start with a letter"
                )

            # Validate failure policy
            if not validate_failure_policy(method.failure_policy):
                errors.append(
                    f"Method '{method.id}': invalid failure policy '{method.failure_policy}'"
                )

            # Validate retry count
            if method.failure_policy == 'retry' and method.retry_count < 1:
                errors.append(
                    f"Method '{method.id}': retry_count must be at least 1"
                )

            # Validate resources
            if method.resources and not validate_resource_spec(method.resources):
                errors.append(
                    f"Method '{method.id}': invalid resource specification"
                )

            if method.inputs and not method.type:
                errors.append(f"Method '{method.id}': normalized inputs require a method type")

        return errors

    def _check_circular_dependencies(self, config: PipelineConfig) -> List[str]:
        """
        Check for circular dependencies in the pipeline graph.

        Args:
            config: PipelineConfig to validate

        Returns:
            List of validation errors
        """
        errors = []

        # Build adjacency list
        graph: Dict[str, List[str]] = {m.id: [] for m in config.methods}
        for conn in config.connections:
            for from_id in conn.from_methods:
                if from_id in graph:  # Should always be true after connection validation
                    graph[from_id].append(conn.to_method)
        for method in config.methods:
            for reference in method.inputs.values():
                if reference.startswith('pipeline.'):
                    continue
                source_method, separator, _ = reference.partition('.')
                if separator and source_method in graph:
                    graph[source_method].append(method.id)

        # Detect cycles using DFS
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def has_cycle(node: str, path: List[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, path.copy()):
                        return True
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    errors.append(
                        f"Circular dependency detected: {' -> '.join(cycle)}"
                    )
                    return True

            rec_stack.remove(node)
            return False

        for method_id in graph:
            if method_id not in visited:
                has_cycle(method_id, [])

        return errors

    def validate_contracts(self, config: PipelineConfig) -> List[str]:
        """Validate artifact declarations and bindings against method contracts."""
        from rapier.methods import get_registry

        errors: List[str] = []
        artifact_registry = get_artifact_registry()
        converter_registry = get_converter_registry()
        method_registry = get_registry()
        method_registry.auto_discover()

        pipeline_artifacts = {}
        for name, artifact_config in config.inputs.items():
            try:
                artifact = artifact_config.to_artifact()
                valid, artifact_errors = artifact_registry.validate(artifact)
                errors.extend(f"Pipeline input '{name}': {error}" for error in artifact_errors)
                if valid:
                    pipeline_artifacts[name] = artifact
            except (TypeError, ValueError, KeyError) as exc:
                errors.append(f"Pipeline input '{name}': {exc}")

        instances: Dict[str, Any] = {}
        for method_config in config.methods:
            if not method_registry.has(method_config.type):
                if method_config.inputs:
                    errors.append(
                        f"Method '{method_config.id}': type '{method_config.type}' is not registered; "
                        "its normalized contract cannot be validated"
                    )
                continue
            method_class = method_registry.get(method_config.type)
            try:
                method = method_class(name=method_config.id, config=method_config.config)
                instances[method_config.id] = method
            except (TypeError, ValueError) as exc:
                errors.append(f"Method '{method_config.id}' configuration: {exc}")

        method_ids = set(config.get_method_ids())
        normalized_pipeline = bool(
            config.inputs or config.outputs or any(item.inputs for item in config.methods)
        )
        for method_config in config.methods:
            method = instances.get(method_config.id)
            if method is None:
                continue
            input_ports = method.input_spec.ports
            # Requirements that depend on configuration (e.g. a filter's position mode).
            errors.extend(
                f"Method '{method_config.id}': {message}"
                for message in method.validate_bindings(method_config.inputs.keys())
            )
            for port_name, port in input_ports.items():
                if port.required and port_name not in method_config.inputs:
                    # Pipelines wired only with `connections` bind data at execution time.
                    if normalized_pipeline:
                        errors.append(
                            f"Method '{method_config.id}': missing binding for required input port "
                            f"'{port_name}'"
                        )
            for port_name, reference in method_config.inputs.items():
                if port_name not in input_ports:
                    errors.append(
                        f"Method '{method_config.id}': unknown input port '{port_name}'"
                    )
                    continue
                source_artifact = None
                if reference.startswith('pipeline.'):
                    input_name = reference[len('pipeline.'):]
                    if not input_name or input_name not in config.inputs:
                        errors.append(
                            f"Method '{method_config.id}' input '{port_name}': unknown pipeline "
                            f"input '{input_name}'"
                        )
                        continue
                    source_artifact = pipeline_artifacts.get(input_name)
                else:
                    source_method, separator, source_port = reference.partition('.')
                    if not separator or not source_method or not source_port:
                        errors.append(
                            f"Method '{method_config.id}' input '{port_name}': invalid reference "
                            f"'{reference}'; use pipeline.<input> or <method>.<output>"
                        )
                        continue
                    if source_method not in method_ids:
                        errors.append(
                            f"Method '{method_config.id}' input '{port_name}': unknown source "
                            f"method '{source_method}'"
                        )
                        continue
                    source_instance = instances.get(source_method)
                    if source_instance is None:
                        continue
                    source_spec = source_instance.output_spec.ports.get(source_port)
                    if source_spec is None:
                        errors.append(
                            f"Method '{method_config.id}' input '{port_name}': method "
                            f"'{source_method}' has no output port '{source_port}'"
                        )
                        continue
                    source_artifact = source_spec.placeholder()

                if source_artifact is not None:
                    destination = input_ports[port_name]
                    if destination.accepts(source_artifact, artifact_registry):
                        continue
                    conversion = converter_registry.find_path(
                        source_artifact.type,
                        source_artifact.format,
                        destination.types,
                        destination.formats,
                    )
                    if conversion is None:
                        errors.append(
                            f"Method '{method_config.id}' input '{port_name}': "
                            f"{source_artifact.type}/{source_artifact.format} is incompatible with "
                            f"types {list(destination.types)} and formats "
                            f"{list(destination.formats) or 'any'}, and no converter is available"
                        )

        for output_name, reference in config.outputs.items():
            source_method, separator, source_port = reference.partition('.')
            if not separator or source_method not in method_ids:
                errors.append(f"Pipeline output '{output_name}': invalid reference '{reference}'")
                continue
            source_instance = instances.get(source_method)
            if source_instance and source_port not in source_instance.output_spec.ports:
                errors.append(
                    f"Pipeline output '{output_name}': method '{source_method}' has no output "
                    f"port '{source_port}'"
                )
        return errors

    def _validate_pooling_rules(self, config: PipelineConfig) -> List[str]:
        """
        Validate pooling rules configuration.

        Args:
            config: PipelineConfig to validate

        Returns:
            List of validation errors
        """
        errors = []

        for method_id, rule in config.pooling_rules.items():
            # Check that method exists
            if not config.has_method(method_id):
                errors.append(
                    f"Pooling rule for non-existent method: '{method_id}'"
                )
                continue

            # Check that method has multiple upstream connections
            upstream = config.get_upstream_methods(method_id)
            if len(upstream) <= 1:
                errors.append(
                    f"Pooling rule for method '{method_id}' but it has "
                    f"{len(upstream)} upstream connection(s). Pooling requires multiple inputs."
                )

            # Validate strategy
            if not validate_pooling_strategy(rule.strategy, rule.metric):
                errors.append(
                    f"Pooling rule for method '{method_id}': invalid strategy or missing metric"
                )

        return errors

    def validate_resources(self, resources: Dict[str, Any]) -> List[str]:
        """
        Validate resource specifications.

        Args:
            resources: Resource specification dictionary

        Returns:
            List of validation errors
        """
        errors = []

        if 'cpus' in resources:
            if not isinstance(resources['cpus'], int) or resources['cpus'] < 1:
                errors.append("CPUs must be a positive integer")

        if 'memory' in resources:
            memory = resources['memory']
            if not isinstance(memory, str) or not any(
                memory.endswith(unit) for unit in ['MB', 'GB', 'TB']
            ):
                errors.append("Memory must be a string with unit (MB, GB, or TB)")

        if 'gpus' in resources:
            if not isinstance(resources['gpus'], int) or resources['gpus'] < 0:
                errors.append("GPUs must be a non-negative integer")

        if 'time_limit' in resources:
            time_limit = resources['time_limit']
            if not isinstance(time_limit, str) or not any(
                time_limit.endswith(unit) for unit in ['s', 'm', 'h', 'd']
            ):
                errors.append("Time limit must be a string with unit (s, m, h, or d)")

        return errors
