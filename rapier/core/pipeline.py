"""
Pipeline class for workflow representation.
"""

from typing import Dict, List, Any, Optional, Tuple
from rapier.core.task import Task
from rapier.core.exceptions import ConfigurationError


class Pipeline:
    """
    Represents a workflow pipeline with tasks and connections.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
    ):
        """
        Initialize pipeline.

        Args:
            name: Pipeline name
            description: Pipeline description
        """
        self.name = name
        self.description = description
        self.tasks: Dict[str, Task] = {}
        self.connections: List[Tuple[str, str]] = []
        self.pooling_rules: Dict[str, str] = {}
        self.metadata: Dict[str, Any] = {}
        self.artifacts: Dict[str, Any] = {}
        self.output_bindings: Dict[str, str] = {}

    def add_task(self, task: Task) -> None:
        """
        Add a task to the pipeline.

        Args:
            task: Task to add

        Raises:
            ConfigurationError: If task_id already exists
        """
        if task.task_id in self.tasks:
            raise ConfigurationError(f"Task with id '{task.task_id}' already exists")
        task.pipeline = self
        self.tasks[task.task_id] = task

    def add_connection(self, from_task_id: str, to_task_id: str) -> None:
        """
        Add a connection between two tasks.

        Args:
            from_task_id: Source task ID
            to_task_id: Destination task ID

        Raises:
            ConfigurationError: If task IDs don't exist
        """
        if from_task_id not in self.tasks:
            raise ConfigurationError(f"Task '{from_task_id}' not found")
        if to_task_id not in self.tasks:
            raise ConfigurationError(f"Task '{to_task_id}' not found")

        connection = (from_task_id, to_task_id)
        if connection not in self.connections:
            self.connections.append(connection)

    def add_pooling_rule(self, task_id: str, rule: str) -> None:
        """
        Add a pooling rule for a task.

        Args:
            task_id: Task ID that pools results
            rule: Pooling rule (e.g., 'all', 'any', 'best[score]')

        Raises:
            ConfigurationError: If task doesn't exist
        """
        if task_id not in self.tasks:
            raise ConfigurationError(f"Task '{task_id}' not found")
        self.pooling_rules[task_id] = rule

    def get_upstream_tasks(self, task_id: str) -> List[str]:
        """
        Get all tasks that feed into the given task.

        Args:
            task_id: Task ID

        Returns:
            List of upstream task IDs
        """
        return [from_id for from_id, to_id in self.connections if to_id == task_id]

    def get_downstream_tasks(self, task_id: str) -> List[str]:
        """
        Get all tasks that depend on the given task.

        Args:
            task_id: Task ID

        Returns:
            List of downstream task IDs
        """
        return [to_id for from_id, to_id in self.connections if from_id == task_id]

    @property
    def pooling_nodes(self) -> List[str]:
        """
        Get list of tasks that pool results from multiple upstream tasks.

        Returns:
            List of task IDs that have pooling rules
        """
        return list(self.pooling_rules.keys())

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate pipeline configuration.

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Check for empty pipeline
        if not self.tasks:
            errors.append("Pipeline has no tasks")
            return False, errors

        # Check for cycles (will be done more thoroughly in ExecutionGraph)
        visited = set()
        rec_stack = set()

        def has_cycle(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)

            for downstream_id in self.get_downstream_tasks(task_id):
                if downstream_id not in visited:
                    if has_cycle(downstream_id):
                        return True
                elif downstream_id in rec_stack:
                    return True

            rec_stack.remove(task_id)
            return False

        for task_id in self.tasks:
            if task_id not in visited:
                if has_cycle(task_id):
                    errors.append("Pipeline contains a cycle")
                    break

        # Check that all tasks in connections exist
        all_task_ids = set(self.tasks.keys())
        for from_id, to_id in self.connections:
            if from_id not in all_task_ids:
                errors.append(f"Connection references non-existent task: {from_id}")
            if to_id not in all_task_ids:
                errors.append(f"Connection references non-existent task: {to_id}")

        # Check that pooling rules reference valid tasks
        for task_id in self.pooling_rules:
            if task_id not in all_task_ids:
                errors.append(f"Pooling rule references non-existent task: {task_id}")

        return len(errors) == 0, errors

    def to_execution_graph(self) -> 'ExecutionGraph':
        """
        Convert pipeline to execution graph.

        Returns:
            ExecutionGraph instance
        """
        from rapier.core.execution_graph import ExecutionGraph
        return ExecutionGraph.from_pipeline(self)

    @classmethod
    def from_config(cls, config: 'PipelineConfig') -> 'Pipeline':
        """
        Create pipeline from configuration.

        Args:
            config: PipelineConfig instance from rapier.config

        Returns:
            Pipeline instance with tasks and connections

        Raises:
            ConfigurationError: If configuration is invalid
        """
        from rapier.config import PipelineConfig
        from rapier.config.validator import ConfigValidator
        from rapier.methods import get_registry

        is_valid, errors = ConfigValidator().validate(config)
        if not is_valid:
            error_msg = "Invalid pipeline configuration:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            raise ConfigurationError(error_msg)

        # Create pipeline
        pipeline = cls(
            name=config.name,
            description=config.description or ""
        )

        # Store metadata from config
        pipeline.metadata['version'] = config.version
        pipeline.metadata['failure_policy'] = config.failure_policy
        pipeline.metadata['environment'] = config.environment
        pipeline.artifacts = {
            name: artifact_config.to_artifact()
            for name, artifact_config in config.inputs.items()
        }
        pipeline.output_bindings = dict(config.outputs)

        registry = get_registry()
        registry.auto_discover()

        # Create tasks from method configurations
        for method_config in config.methods:
            method = None
            if registry.has(method_config.type):
                method_class = registry.get(method_config.type)
                method = method_class.from_config({
                    'name': method_config.id,
                    'version': config.version or '1.0',
                    'parameters': method_config.config,
                    'config': method_config.config,
                })
                if getattr(method, 'contract_version', 1) >= 2:
                    method.normalize_config(method_config.config)

            task = Task(
                task_id=method_config.id,
                method=method,
                inputs={},
                metadata={
                    'method_type': method_config.type,
                    'description': method_config.description,
                    'config': method_config.config,
                    'parameters': method_config.config,
                    'input_bindings': dict(method_config.inputs),
                    'resources': method_config.resources or {},
                    'failure_policy': method_config.failure_policy,
                }
            )

            # Set retry configuration
            if method_config.failure_policy == 'retry':
                task.max_retries = method_config.retry_count
                # Convert string policy to enum
                from rapier.core.enums import FailurePolicy
                task.failure_policy = FailurePolicy.RETRY
            elif method_config.failure_policy == 'continue':
                from rapier.core.enums import FailurePolicy
                task.failure_policy = FailurePolicy.CONTINUE
            else:
                from rapier.core.enums import FailurePolicy
                task.failure_policy = FailurePolicy.STOP

            pipeline.add_task(task)

        # Add connections
        for conn_config in config.connections:
            # Handle multiple from_methods (parallel connections)
            for from_id in conn_config.from_methods:
                pipeline.add_connection(from_id, conn_config.to_method)

        # Data references are also execution dependencies.
        for method_config in config.methods:
            for reference in method_config.inputs.values():
                if reference.startswith('pipeline.'):
                    continue
                source_method, separator, _ = reference.partition('.')
                if separator and source_method in pipeline.tasks:
                    pipeline.add_connection(source_method, method_config.id)

        # Add pooling rules
        for method_id, pooling_rule_config in config.pooling_rules.items():
            # Convert pooling rule to string format
            if pooling_rule_config.strategy == 'best':
                rule_str = f"best[{pooling_rule_config.metric}]"
                if pooling_rule_config.minimize:
                    rule_str += ":minimize"
            elif pooling_rule_config.strategy == 'custom':
                rule_str = f"custom[{pooling_rule_config.function}]"
            else:
                rule_str = pooling_rule_config.strategy

            pipeline.add_pooling_rule(method_id, rule_str)

        # Validate the constructed pipeline
        is_valid, errors = pipeline.validate()
        if not is_valid:
            error_msg = "Invalid pipeline configuration:\n" + "\n".join(f"  - {e}" for e in errors)
            raise ConfigurationError(error_msg)

        return pipeline

    def __repr__(self) -> str:
        return f"Pipeline(name={self.name}, tasks={len(self.tasks)}, connections={len(self.connections)})"
