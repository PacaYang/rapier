"""
SLURM job dependency builder.

Converts task dependencies to SLURM dependency strings.
"""

from typing import List, Optional, Dict, Any
from .job import SlurmJob


class DependencyBuilder:
    """
    Builder for SLURM job dependency strings.

    Converts pipeline task dependencies to SLURM dependency syntax.
    """

    # SLURM dependency types
    DEPENDENCY_TYPES = {
        'afterok': 'afterok',        # Start after specified jobs complete successfully
        'afterany': 'afterany',      # Start after specified jobs complete (any exit code)
        'afternotok': 'afternotok',  # Start after specified jobs fail
        'aftercorr': 'aftercorr',    # Start after corresponding job ID in array completes
        'singleton': 'singleton',    # Only one job with this name can run at a time
    }

    def build_dependency_string(
        self,
        upstream_jobs: List[SlurmJob],
        policy: str = 'afterok'
    ) -> Optional[str]:
        """
        Build SLURM dependency string from upstream jobs.

        Args:
            upstream_jobs: List of upstream SlurmJob instances
            policy: Dependency policy ('afterok', 'afterany', 'afternotok')

        Returns:
            Dependency string (e.g., "afterok:12345:12346") or None

        Examples:
            >>> job1 = SlurmJob(job_name='task_a', job_id='12345')
            >>> job2 = SlurmJob(job_name='task_b', job_id='12346')
            >>> builder = DependencyBuilder()
            >>> builder.build_dependency_string([job1, job2], 'afterok')
            'afterok:12345:12346'
        """
        if not upstream_jobs:
            return None

        # Filter out jobs without IDs
        job_ids = [job.job_id for job in upstream_jobs if job.job_id]

        if not job_ids:
            return None

        # Validate policy
        if policy not in self.DEPENDENCY_TYPES:
            raise ValueError(f"Invalid dependency policy: {policy}")

        dependency_type = self.DEPENDENCY_TYPES[policy]

        # Build dependency string
        return f"{dependency_type}:{':'.join(job_ids)}"

    def build_from_failure_policy(
        self,
        upstream_jobs: List[SlurmJob],
        failure_policy: str
    ) -> Optional[str]:
        """
        Build dependency string based on failure policy.

        Maps platform failure policies to SLURM dependency types:
        - 'stop': afterok (require success)
        - 'continue': afterany (run regardless)
        - 'retry': afterok (require success, retry handled separately)

        Args:
            upstream_jobs: List of upstream jobs
            failure_policy: Failure policy ('stop', 'continue', 'retry')

        Returns:
            Dependency string or None
        """
        policy_map = {
            'stop': 'afterok',
            'continue': 'afterany',
            'retry': 'afterok',
        }

        slurm_policy = policy_map.get(failure_policy, 'afterok')
        return self.build_dependency_string(upstream_jobs, slurm_policy)

    def build_complex_dependency(
        self,
        afterok: Optional[List[SlurmJob]] = None,
        afterany: Optional[List[SlurmJob]] = None,
        afternotok: Optional[List[SlurmJob]] = None
    ) -> Optional[str]:
        """
        Build complex dependency with multiple conditions.

        Combines different dependency types with '?' separator.

        Args:
            afterok: Jobs that must succeed
            afterany: Jobs that must complete (any status)
            afternotok: Jobs that must fail

        Returns:
            Complex dependency string or None

        Example:
            >>> # Start after job1 succeeds AND job2 completes
            >>> builder.build_complex_dependency(
            ...     afterok=[job1],
            ...     afterany=[job2]
            ... )
            'afterok:12345?afterany:12346'
        """
        parts = []

        if afterok:
            dep = self.build_dependency_string(afterok, 'afterok')
            if dep:
                parts.append(dep)

        if afterany:
            dep = self.build_dependency_string(afterany, 'afterany')
            if dep:
                parts.append(dep)

        if afternotok:
            dep = self.build_dependency_string(afternotok, 'afternotok')
            if dep:
                parts.append(dep)

        if not parts:
            return None

        return '?'.join(parts)

    def build_singleton_dependency(self, job_name: str) -> str:
        """
        Build singleton dependency.

        Ensures only one job with this name runs at a time.

        Args:
            job_name: Job name

        Returns:
            Singleton dependency string
        """
        return f"singleton:{job_name}"

    def parse_dependency_string(self, dep_string: str) -> List[Dict[str, Any]]:
        """
        Parse SLURM dependency string into components.

        Args:
            dep_string: Dependency string (e.g., "afterok:12345:12346")

        Returns:
            List of dependency specifications

        Example:
            >>> builder.parse_dependency_string("afterok:12345:12346")
            [{'type': 'afterok', 'job_ids': ['12345', '12346']}]
        """
        if not dep_string:
            return []

        dependencies = []

        # Split by '?' for complex dependencies
        for part in dep_string.split('?'):
            part = part.strip()
            if ':' not in part:
                continue

            # Parse type:id:id:...
            components = part.split(':')
            dep_type = components[0]
            job_ids = components[1:]

            dependencies.append({
                'type': dep_type,
                'job_ids': job_ids
            })

        return dependencies

    def validate_dependency_string(self, dep_string: str) -> bool:
        """
        Validate SLURM dependency string syntax.

        Args:
            dep_string: Dependency string

        Returns:
            True if valid, False otherwise
        """
        if not dep_string:
            return True

        try:
            deps = self.parse_dependency_string(dep_string)
            for dep in deps:
                if dep['type'] not in self.DEPENDENCY_TYPES.values():
                    return False
                if not dep['job_ids']:
                    return False
            return True
        except Exception:
            return False

    def get_upstream_job_ids(
        self,
        jobs: List[SlurmJob],
        task_id: str,
        connections: List[tuple]
    ) -> List[str]:
        """
        Get job IDs of upstream tasks for a given task.

        Args:
            jobs: List of all SlurmJob instances
            task_id: Target task ID
            connections: List of (from_task_id, to_task_id) tuples

        Returns:
            List of upstream job IDs
        """
        # Find upstream task IDs
        upstream_task_ids = [
            from_id for from_id, to_id in connections
            if to_id == task_id
        ]

        # Map task IDs to job IDs
        job_map = {job.job_name: job.job_id for job in jobs if job.job_id}

        upstream_job_ids = [
            job_map[task_id] for task_id in upstream_task_ids
            if task_id in job_map
        ]

        return upstream_job_ids
