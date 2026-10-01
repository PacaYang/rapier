"""
Result pooling from multiple upstream tasks.

Combines results from parallel branches according to pooling rules.
"""

from typing import List, Any, Dict, Optional, Callable
from rapier.artifacts import Artifact
from rapier.core.task import Task


class ResultPooler:
    """
    Pools results from multiple upstream tasks.

    Implements various pooling strategies:
    - any: Accept any successful result
    - all: Combine all successful results
    - majority: Require majority success
    - best: Select best result by metric
    - first: Use first completed result
    - custom: User-defined pooling function
    """

    def __init__(self):
        """Initialize result pooler."""
        self.custom_functions: Dict[str, Callable] = {}

    def pool_results(
        self,
        tasks: List[Task],
        rule: str,
        metric: Optional[str] = None,
        minimize: bool = False
    ) -> Any:
        """
        Pool results from multiple tasks according to rule.

        Args:
            tasks: List of tasks to pool results from
            rule: Pooling rule ('any', 'all', 'majority', 'best', 'first', 'custom')
            metric: Metric name for 'best' strategy
            minimize: Whether to minimize metric (default: maximize)

        Returns:
            Pooled result

        Raises:
            ValueError: If rule is invalid or tasks list is empty
        """
        if not tasks:
            raise ValueError("Cannot pool results from empty task list")

        # Handle custom rules first, since their bracket contains a function
        # name (not a metric) and must not be consumed by the generic parser.
        if rule.startswith('custom'):
            if '[' in rule:
                func_name = rule.split('[', 1)[1].rstrip(']')
            else:
                raise ValueError("'custom' rule requires function name: custom[func_name]")
            return self.apply_custom_rule(tasks, func_name)

        # Parse rule (handle 'best[metric]' format)
        if '[' in rule:
            # Extract metric from rule like 'best[score]'
            rule_type, metric_part = rule.split('[', 1)
            metric = metric_part.rstrip(']')
            rule = rule_type

        # Apply pooling strategy
        if rule == 'any':
            return self.apply_rule_any(tasks)
        elif rule == 'all':
            return self.apply_rule_all(tasks)
        elif rule == 'majority':
            return self.apply_rule_majority(tasks)
        elif rule == 'best':
            if not metric:
                raise ValueError("'best' rule requires a metric")
            return self.apply_rule_best(tasks, metric, minimize)
        elif rule == 'first':
            return self.apply_rule_first(tasks)
        else:
            raise ValueError(f"Unknown pooling rule: {rule}")

    def apply_rule_any(self, tasks: List[Task]) -> List[Task]:
        """
        Accept any successful result.

        Returns first successful task's results.

        Args:
            tasks: List of tasks

        Returns:
            First successful task or empty list
        """
        from rapier.core.enums import TaskStatus

        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                return [task]

        return []

    def apply_rule_all(self, tasks: List[Task]) -> List[Task]:
        """
        Combine all successful results.

        Args:
            tasks: List of tasks

        Returns:
            List of all successful tasks
        """
        from rapier.core.enums import TaskStatus

        successful_tasks = [
            task for task in tasks
            if task.status == TaskStatus.COMPLETED
        ]

        return successful_tasks

    def apply_rule_majority(self, tasks: List[Task]) -> List[Task]:
        """
        Require majority success.

        Returns all successful tasks if >50% succeeded.

        Args:
            tasks: List of tasks

        Returns:
            List of successful tasks if majority succeeded, empty list otherwise
        """
        from rapier.core.enums import TaskStatus

        successful_tasks = [
            task for task in tasks
            if task.status == TaskStatus.COMPLETED
        ]

        # Require more than 50% success
        if len(successful_tasks) > len(tasks) / 2:
            return successful_tasks

        return []

    def apply_rule_best(
        self,
        tasks: List[Task],
        metric: str,
        minimize: bool = False
    ) -> Task:
        """
        Select best result by metric value.

        Args:
            tasks: List of tasks
            metric: Metric name in task outputs
            minimize: Whether to minimize (default: maximize)

        Returns:
            Task with best metric value

        Raises:
            ValueError: If no tasks have the metric
        """
        from rapier.core.enums import TaskStatus

        # Filter successful tasks
        successful_tasks = [
            task for task in tasks
            if task.status == TaskStatus.COMPLETED
        ]

        if not successful_tasks:
            raise ValueError("No successful tasks to select from")

        # Find tasks with metric
        tasks_with_metric = []
        for task in successful_tasks:
            if metric in task.outputs:
                tasks_with_metric.append(task)

        if not tasks_with_metric:
            raise ValueError(f"No tasks have metric '{metric}' in outputs")

        def metric_value(task):
            value = task.outputs[metric]
            if isinstance(value, Artifact):
                if "value" not in value.metadata:
                    raise ValueError(
                        f"Artifact metric '{metric}' must define numeric metadata['value']"
                    )
                value = value.metadata["value"]
            if not isinstance(value, (int, float)):
                raise ValueError(f"Metric '{metric}' must be numeric")
            return value

        if minimize:
            best_task = min(tasks_with_metric, key=metric_value)
        else:
            best_task = max(tasks_with_metric, key=metric_value)

        return best_task

    def apply_rule_first(self, tasks: List[Task]) -> Task:
        """
        Use first completed result.

        Args:
            tasks: List of tasks (assumed sorted by completion time)

        Returns:
            First completed task

        Raises:
            ValueError: If no tasks completed
        """
        from rapier.core.enums import TaskStatus

        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                return task

        raise ValueError("No completed tasks")

    def apply_custom_rule(self, tasks: List[Task], function_name: str) -> Any:
        """
        Apply custom pooling function.

        Args:
            tasks: List of tasks
            function_name: Name of registered custom function

        Returns:
            Result from custom function

        Raises:
            ValueError: If function not registered
        """
        if function_name not in self.custom_functions:
            raise ValueError(f"Custom pooling function '{function_name}' not registered")

        func = self.custom_functions[function_name]
        return func(tasks)

    def register_custom_function(self, name: str, func: Callable):
        """
        Register a custom pooling function.

        Args:
            name: Function name
            func: Callable that takes List[Task] and returns pooled result
        """
        self.custom_functions[name] = func

    def merge_outputs(
        self,
        tasks: List[Task],
        merge_strategy: str = 'concat'
    ) -> Dict[str, Any]:
        """
        Merge outputs from multiple tasks.

        Args:
            tasks: List of tasks with outputs
            merge_strategy: How to merge ('concat', 'union', 'intersect')

        Returns:
            Merged outputs dictionary
        """
        from rapier.core.enums import TaskStatus

        # Filter successful tasks
        successful_tasks = [
            task for task in tasks
            if task.status == TaskStatus.COMPLETED and task.outputs
        ]

        if not successful_tasks:
            return {}

        merged = {}

        # Get all output keys
        all_keys = set()
        for task in successful_tasks:
            all_keys.update(task.outputs.keys())

        # Merge each key
        for key in all_keys:
            values = [task.outputs[key] for task in successful_tasks if key in task.outputs]

            if not values:
                continue

            # Determine merge strategy based on value type
            if isinstance(values[0], Artifact):
                # Domain-specific methods, not the generic pooler, merge payloads.
                merged[key] = values if len(values) > 1 else values[0]
            elif isinstance(values[0], list):
                if merge_strategy == 'concat':
                    merged[key] = sum(values, [])  # Concatenate lists
                elif merge_strategy == 'union':
                    combined = sum(values, [])
                    if combined and isinstance(combined[0], Artifact):
                        merged[key] = list({item.artifact_id: item for item in combined}.values())
                    else:
                        merged[key] = list(set(combined))  # Union of lists
                else:
                    merged[key] = values[0]  # Just take first
            elif isinstance(values[0], (int, float)):
                # For numbers, take average or sum
                merged[key] = sum(values) / len(values)
            else:
                # For other types, take first value
                merged[key] = values[0]

        return merged
