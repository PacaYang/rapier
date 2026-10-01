#!/usr/bin/env python3
"""Command-line tool to validate a Rapier pipeline configuration.

Usage:
    python scripts/validate_config.py <pipeline.yaml>
    python scripts/validate_config.py <pipeline.yaml> --build

Exit codes:
    0  configuration is valid
    1  configuration is invalid
    2  usage error / file not found
"""

import argparse
import sys
from pathlib import Path

# Make the package importable when run from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from rapier.config.parser import ConfigParser
from rapier.config.validator import ConfigValidator


def validate_config(config_path: str, build: bool = False) -> int:
    """Validate a pipeline configuration file.

    Args:
        config_path: Path to the YAML/JSON configuration file
        build: If True, also attempt to build the Pipeline object

    Returns:
        Process exit code
    """
    path = Path(config_path)
    if not path.exists():
        print(f"Error: configuration file not found: {config_path}", file=sys.stderr)
        return 2

    # Parse
    try:
        parser = ConfigParser()
        config = parser.parse_file(str(path))
    except Exception as e:
        print(f"✗ Failed to parse configuration: {e}", file=sys.stderr)
        return 1

    # Validate
    validator = ConfigValidator()
    is_valid, errors = validator.validate(config)

    if not is_valid:
        print(f"✗ Configuration is INVALID: {config_path}")
        for error in errors:
            print(f"    - {error}")
        return 1

    print(f"✓ Configuration is valid: {config.name}")
    print(f"    Methods: {len(config.methods)}")
    print(f"    Connections: {len(config.connections)}")
    if config.pooling_rules:
        print(f"    Pooling rules: {len(config.pooling_rules)}")

    # Optionally build the pipeline object
    if build:
        try:
            from rapier.core.pipeline import Pipeline
            pipeline = Pipeline.from_config(config)
            print(f"    Built pipeline: {pipeline}")
        except Exception as e:
            print(f"✗ Failed to build pipeline: {e}", file=sys.stderr)
            return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Validate a Rapier pipeline configuration file."
    )
    parser.add_argument("config", help="Path to the pipeline YAML/JSON file")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Also build the Pipeline object to verify full construction",
    )
    args = parser.parse_args()

    sys.exit(validate_config(args.config, build=args.build))


if __name__ == "__main__":
    main()
