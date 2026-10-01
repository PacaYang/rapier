"""
Resource specification parser for SLURM.

Handles parsing and validation of resource specifications like memory,
time limits, CPUs, and GPUs.
"""

import re
from typing import Dict, Any, Optional


class ResourceParser:
    """
    Parser for SLURM resource specifications.

    Converts human-readable resource specs to SLURM-compatible formats.
    """

    # Memory unit conversion to MB
    MEMORY_UNITS = {
        'KB': 1 / 1024,
        'MB': 1,
        'GB': 1024,
        'TB': 1024 * 1024,
    }

    # Time unit conversion to seconds
    TIME_UNITS = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
    }

    def parse_memory(self, spec: str) -> int:
        """
        Parse memory specification to megabytes.

        Args:
            spec: Memory specification (e.g., "4GB", "512MB", "2TB")

        Returns:
            Memory in megabytes

        Raises:
            ValueError: If spec format is invalid

        Examples:
            >>> parser.parse_memory("4GB")
            4096
            >>> parser.parse_memory("512MB")
            512
        """
        if isinstance(spec, int):
            return spec

        spec = spec.upper().strip()

        # Match number and unit
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGT]?B)$', spec)
        if not match:
            raise ValueError(f"Invalid memory specification: {spec}")

        value, unit = match.groups()
        value = float(value)

        if unit not in self.MEMORY_UNITS:
            raise ValueError(f"Unknown memory unit: {unit}")

        return int(value * self.MEMORY_UNITS[unit])

    def parse_time(self, spec: str) -> int:
        """
        Parse time specification to seconds.

        Supports both SLURM format (HH:MM:SS) and simple format (1h, 30m).

        Args:
            spec: Time specification (e.g., "2:30:00", "1h", "90m")

        Returns:
            Time in seconds

        Raises:
            ValueError: If spec format is invalid

        Examples:
            >>> parser.parse_time("2:30:00")
            9000
            >>> parser.parse_time("1h")
            3600
            >>> parser.parse_time("90m")
            5400
        """
        if isinstance(spec, int):
            return spec

        spec = spec.strip()

        # Try SLURM format: HH:MM:SS or DD-HH:MM:SS
        if ':' in spec:
            return self._parse_slurm_time(spec)

        # Try simple format: 1h, 30m, 90s
        match = re.match(r'^(\d+)\s*([smhd])$', spec.lower())
        if not match:
            raise ValueError(f"Invalid time specification: {spec}")

        value, unit = match.groups()
        value = int(value)

        if unit not in self.TIME_UNITS:
            raise ValueError(f"Unknown time unit: {unit}")

        return value * self.TIME_UNITS[unit]

    def _parse_slurm_time(self, spec: str) -> int:
        """
        Parse SLURM time format.

        Formats:
        - MM:SS
        - HH:MM:SS
        - DD-HH:MM:SS

        Args:
            spec: SLURM time specification

        Returns:
            Time in seconds
        """
        days = 0

        # Check for days
        if '-' in spec:
            day_part, time_part = spec.split('-', 1)
            days = int(day_part)
            spec = time_part

        # Parse time components
        parts = spec.split(':')
        if len(parts) == 2:
            minutes, seconds = map(int, parts)
            hours = 0
        elif len(parts) == 3:
            hours, minutes, seconds = map(int, parts)
        else:
            raise ValueError(f"Invalid SLURM time format: {spec}")

        total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return total_seconds

    def parse_cpus(self, spec: Any) -> int:
        """
        Parse CPU specification.

        Args:
            spec: CPU specification (integer or string)

        Returns:
            Number of CPUs

        Raises:
            ValueError: If spec is invalid

        Examples:
            >>> parser.parse_cpus(4)
            4
            >>> parser.parse_cpus("8")
            8
        """
        if isinstance(spec, int):
            if spec < 1:
                raise ValueError(f"CPUs must be at least 1, got {spec}")
            return spec

        try:
            cpus = int(spec)
            if cpus < 1:
                raise ValueError(f"CPUs must be at least 1, got {cpus}")
            return cpus
        except ValueError:
            raise ValueError(f"Invalid CPU specification: {spec}")

    def parse_gpus(self, spec: Any) -> Dict[str, Any]:
        """
        Parse GPU specification.

        Formats:
        - Integer: number of GPUs (any type)
        - String: "2" or "2xV100" or "V100:2"

        Args:
            spec: GPU specification

        Returns:
            Dictionary with 'count' and optional 'type'

        Examples:
            >>> parser.parse_gpus(2)
            {'count': 2, 'type': None}
            >>> parser.parse_gpus("2xV100")
            {'count': 2, 'type': 'V100'}
        """
        if isinstance(spec, int):
            if spec < 0:
                raise ValueError(f"GPU count must be non-negative, got {spec}")
            return {'count': spec, 'type': None}

        if isinstance(spec, dict):
            return spec

        spec = str(spec).strip()

        # Try "2xV100" format
        match = re.match(r'^(\d+)\s*x\s*([A-Za-z0-9]+)$', spec, re.IGNORECASE)
        if match:
            count, gpu_type = match.groups()
            return {'count': int(count), 'type': gpu_type}

        # Try "V100:2" format
        match = re.match(r'^([A-Za-z0-9]+)\s*:\s*(\d+)$', spec)
        if match:
            gpu_type, count = match.groups()
            return {'count': int(count), 'type': gpu_type}

        # Try plain number
        try:
            count = int(spec)
            if count < 0:
                raise ValueError(f"GPU count must be non-negative, got {count}")
            return {'count': count, 'type': None}
        except ValueError:
            raise ValueError(f"Invalid GPU specification: {spec}")

    def format_time_for_slurm(self, seconds: int) -> str:
        """
        Format seconds to SLURM time format (HH:MM:SS or DD-HH:MM:SS).

        Args:
            seconds: Time in seconds

        Returns:
            SLURM time string

        Examples:
            >>> parser.format_time_for_slurm(3661)
            '01:01:01'
            >>> parser.format_time_for_slurm(90000)
            '1-01:00:00'
        """
        days = seconds // 86400
        seconds %= 86400
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60

        if days > 0:
            return f"{days}-{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def validate_resources(
        self,
        resources: Dict[str, Any],
        limits: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, list[str]]:
        """
        Validate resource specifications against cluster limits.

        Args:
            resources: Resource specifications
            limits: Optional cluster resource limits

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Validate CPU count
        if 'cpus' in resources:
            try:
                cpus = self.parse_cpus(resources['cpus'])
                if limits and 'max_cpus' in limits:
                    if cpus > limits['max_cpus']:
                        errors.append(
                            f"CPU count {cpus} exceeds limit {limits['max_cpus']}"
                        )
            except ValueError as e:
                errors.append(str(e))

        # Validate memory
        if 'memory' in resources:
            try:
                memory_mb = self.parse_memory(resources['memory'])
                if limits and 'max_memory_mb' in limits:
                    if memory_mb > limits['max_memory_mb']:
                        errors.append(
                            f"Memory {memory_mb}MB exceeds limit {limits['max_memory_mb']}MB"
                        )
            except ValueError as e:
                errors.append(str(e))

        # Validate time
        if 'time_limit' in resources:
            try:
                time_sec = self.parse_time(resources['time_limit'])
                if limits and 'max_time_sec' in limits:
                    if time_sec > limits['max_time_sec']:
                        errors.append(
                            f"Time limit {time_sec}s exceeds limit {limits['max_time_sec']}s"
                        )
            except ValueError as e:
                errors.append(str(e))

        # Validate GPUs
        if 'gpus' in resources:
            try:
                gpu_spec = self.parse_gpus(resources['gpus'])
                if limits and 'max_gpus' in limits:
                    if gpu_spec['count'] > limits['max_gpus']:
                        errors.append(
                            f"GPU count {gpu_spec['count']} exceeds limit {limits['max_gpus']}"
                        )
            except ValueError as e:
                errors.append(str(e))

        return (len(errors) == 0, errors)
