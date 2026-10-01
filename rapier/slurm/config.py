"""
SLURM cluster configuration reader.

Queries SLURM cluster information and provides configuration details.
"""

import subprocess
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class PartitionInfo:
    """Information about a SLURM partition."""
    name: str
    state: str
    max_time: Optional[int] = None  # seconds
    max_cpus: Optional[int] = None
    max_memory: Optional[int] = None  # MB
    nodes: List[str] = field(default_factory=list)
    default: bool = False


class SlurmConfig:
    """
    SLURM cluster configuration reader.

    Queries SLURM commands to gather cluster information.
    """

    def __init__(self):
        """Initialize SLURM configuration reader."""
        self.partitions: Dict[str, PartitionInfo] = {}
        self.default_partition: Optional[str] = None
        self._slurm_available: Optional[bool] = None

    def is_slurm_available(self) -> bool:
        """
        Check if SLURM commands are available.

        Returns:
            True if SLURM is available, False otherwise
        """
        if self._slurm_available is not None:
            return self._slurm_available

        try:
            result = subprocess.run(
                ['sinfo', '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            self._slurm_available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._slurm_available = False

        return self._slurm_available

    def read_cluster_info(self) -> bool:
        """
        Read cluster information from SLURM.

        Returns:
            True if successful, False otherwise
        """
        if not self.is_slurm_available():
            return False

        try:
            self._read_partitions()
            return True
        except Exception:
            return False

    def _read_partitions(self):
        """Read partition information from sinfo."""
        try:
            # Query partition information
            result = subprocess.run(
                ['sinfo', '-o', '%P|%a|%l|%N', '--noheader'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return

            # Parse output
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('|')
                if len(parts) < 4:
                    continue

                partition_name = parts[0].rstrip('*')
                is_default = parts[0].endswith('*')
                state = parts[1]
                time_limit = parts[2]
                nodes = parts[3].split(',')

                # Parse time limit
                max_time = self._parse_time_limit(time_limit)

                partition = PartitionInfo(
                    name=partition_name,
                    state=state,
                    max_time=max_time,
                    nodes=nodes,
                    default=is_default
                )

                self.partitions[partition_name] = partition

                if is_default:
                    self.default_partition = partition_name

        except Exception:
            pass

    def _parse_time_limit(self, time_str: str) -> Optional[int]:
        """
        Parse SLURM time limit string to seconds.

        Args:
            time_str: Time string (e.g., "1-00:00:00", "infinite")

        Returns:
            Time in seconds or None
        """
        if time_str.lower() == 'infinite':
            return None

        try:
            # Parse DD-HH:MM:SS or HH:MM:SS
            days = 0
            if '-' in time_str:
                day_part, time_part = time_str.split('-', 1)
                days = int(day_part)
                time_str = time_part

            parts = time_str.split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
            elif len(parts) == 2:
                hours, minutes = map(int, parts)
                seconds = 0
            else:
                return None

            return days * 86400 + hours * 3600 + minutes * 60 + seconds

        except Exception:
            return None

    def get_partitions(self) -> List[str]:
        """
        Get list of available partition names.

        Returns:
            List of partition names
        """
        return list(self.partitions.keys())

    def get_partition_info(self, partition: str) -> Optional[PartitionInfo]:
        """
        Get information about a specific partition.

        Args:
            partition: Partition name

        Returns:
            PartitionInfo or None if not found
        """
        return self.partitions.get(partition)

    def validate_partition(self, partition: str) -> bool:
        """
        Check if a partition exists.

        Args:
            partition: Partition name

        Returns:
            True if partition exists, False otherwise
        """
        if not self.partitions:
            self.read_cluster_info()

        return partition in self.partitions

    def get_partition_limits(self, partition: str) -> Optional[Dict[str, Any]]:
        """
        Get resource limits for a partition.

        Args:
            partition: Partition name

        Returns:
            Dictionary with resource limits or None
        """
        info = self.get_partition_info(partition)
        if not info:
            return None

        limits = {}
        if info.max_time is not None:
            limits['max_time_sec'] = info.max_time
        if info.max_cpus is not None:
            limits['max_cpus'] = info.max_cpus
        if info.max_memory is not None:
            limits['max_memory_mb'] = info.max_memory

        return limits

    def get_default_partition(self) -> Optional[str]:
        """
        Get the default partition name.

        Returns:
            Default partition name or None
        """
        if not self.partitions:
            self.read_cluster_info()

        return self.default_partition

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize configuration to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            'slurm_available': self.is_slurm_available(),
            'default_partition': self.default_partition,
            'partitions': {
                name: {
                    'state': info.state,
                    'max_time': info.max_time,
                    'max_cpus': info.max_cpus,
                    'max_memory': info.max_memory,
                    'nodes': info.nodes,
                    'default': info.default,
                }
                for name, info in self.partitions.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SlurmConfig':
        """
        Deserialize configuration from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            SlurmConfig instance
        """
        config = cls()
        config._slurm_available = data.get('slurm_available')
        config.default_partition = data.get('default_partition')

        for name, info_dict in data.get('partitions', {}).items():
            config.partitions[name] = PartitionInfo(
                name=name,
                state=info_dict.get('state', 'unknown'),
                max_time=info_dict.get('max_time'),
                max_cpus=info_dict.get('max_cpus'),
                max_memory=info_dict.get('max_memory'),
                nodes=info_dict.get('nodes', []),
                default=info_dict.get('default', False)
            )

        return config
