#!/usr/bin/env python3
"""
Demonstration of Stage 2 Pipeline Configuration System

This script demonstrates all major features of the configuration system:
- Parsing YAML configurations
- Validating configurations
- Converting to Pipeline objects
- Template generation
- Variable substitution
- Error handling
"""

from rapier.config import (
    ConfigParser,
    ConfigValidator,
    create_simple_pipeline,
    create_parallel_pipeline,
    create_pooling_pipeline,
    create_diamond_pipeline
)
from rapier.core import Pipeline
import sys


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def demo_parse_and_validate():
    """Demonstrate parsing and validating a configuration file."""
    print_section("1. Parse and Validate Configuration")

    parser = ConfigParser()
    validator = ConfigValidator()

    # Parse the parallel pooling example
    config = parser.parse_file('examples/pipelines/parallel_pooling.yaml')

    print(f"Pipeline: {config.name}")
    print(f"Description: {config.description}")
    print(f"Version: {config.version}")
    print(f"\nMethods ({len(config.methods)}):")
    for method in config.methods:
        print(f"  - {method.id}: {method.type}")
        if method.resources:
            print(f"    Resources: {method.resources}")

    print(f"\nConnections ({len(config.connections)}):")
    for conn in config.connections:
        print(f"  - {conn.from_methods} -> {conn.to_method}")

    print(f"\nPooling Rules:")
    for method_id, rule in config.pooling_rules.items():
        print(f"  - {method_id}: strategy={rule.strategy}, metric={rule.metric}")

    # Validate
    is_valid, errors = validator.validate(config)
    print(f"\nValidation: {'✓ PASSED' if is_valid else '✗ FAILED'}")
    if errors:
        for error in errors:
            print(f"  - {error}")


def demo_convert_to_pipeline():
    """Demonstrate converting configuration to Pipeline object."""
    print_section("2. Convert Configuration to Pipeline")

    parser = ConfigParser()
    config = parser.parse_file('examples/pipelines/simple_serial.yaml')

    # Convert to pipeline
    pipeline = Pipeline.from_config(config)

    print(f"Pipeline: {pipeline.name}")
    print(f"Description: {pipeline.description}")
    print(f"Tasks: {len(pipeline.tasks)}")
    print(f"Connections: {len(pipeline.connections)}")

    print(f"\nTask Details:")
    for task_id, task in pipeline.tasks.items():
        print(f"  - {task_id}")
        print(f"    Type: {task.metadata.get('method_type')}")
        print(f"    Failure Policy: {task.failure_policy.name}")
        if task.max_retries > 0:
            print(f"    Max Retries: {task.max_retries}")

    print(f"\nExecution Order:")
    for i, (from_id, to_id) in enumerate(pipeline.connections, 1):
        print(f"  {i}. {from_id} -> {to_id}")


def demo_templates():
    """Demonstrate template pipeline generation."""
    print_section("3. Generate Pipelines from Templates")

    # Simple serial pipeline
    config1 = create_simple_pipeline(
        "quick_screening",
        ["filtering", "docking", "scoring"]
    )
    print(f"Simple Serial: {config1.name}")
    print(f"  Methods: {[m.id for m in config1.methods]}")

    # Parallel pipeline
    config2 = create_parallel_pipeline(
        "multi_docking",
        initial_method="preparation",
        parallel_methods=["vina", "autodock", "smina"],
        final_method="consensus"
    )
    print(f"\nParallel: {config2.name}")
    print(f"  Methods: {[m.id for m in config2.methods]}")

    # Pooling pipeline
    config3 = create_pooling_pipeline(
        "consensus_screening",
        initial_methods=["method_a", "method_b"],
        pooling_method="consensus",
        pooling_strategy="best",
        metric="docking_score"
    )
    print(f"\nPooling: {config3.name}")
    print(f"  Methods: {[m.id for m in config3.methods]}")
    print(f"  Pooling: {list(config3.pooling_rules.keys())}")

    # Diamond pipeline
    config4 = create_diamond_pipeline(
        "full_workflow",
        preparation_method="prep",
        parallel_methods=["vina", "autodock"],
        pooling_method="consensus",
        final_method="analysis",
        pooling_strategy="majority"
    )
    print(f"\nDiamond: {config4.name}")
    print(f"  Methods: {[m.id for m in config4.methods]}")


def demo_variable_substitution():
    """Demonstrate variable substitution."""
    print_section("4. Variable Substitution")

    import os
    os.environ['TARGET_PROTEIN'] = 'kinase_receptor'

    parser = ConfigParser()
    parser.set_variable('DATA_PATH', '/shared/data')
    parser.set_variable('OUTPUT_PATH', '/results')

    config_str = """
name: var_demo
methods:
  - id: docking
    type: vina_dock
    parameters:
      receptor: ${DATA_PATH}/${TARGET_PROTEIN}.pdbqt
      output: ${OUTPUT_PATH}/${TARGET_PROTEIN}/
      project_data: ${PROJECT_DIR}/data
"""

    config = parser.parse_string(config_str)
    params = config.methods[0].parameters

    print("Variables defined:")
    print(f"  Environment: TARGET_PROTEIN = 'kinase_receptor'")
    print(f"  Custom: DATA_PATH = '/shared/data'")
    print(f"  Custom: OUTPUT_PATH = '/results'")
    print(f"  Built-in: PROJECT_DIR (auto-detected)")

    print("\nSubstituted parameters:")
    print(f"  receptor: {params['receptor']}")
    print(f"  output: {params['output']}")
    print(f"  project_data: {params['project_data']}")


def demo_error_handling():
    """Demonstrate error detection and handling."""
    print_section("5. Error Detection and Handling")

    parser = ConfigParser()
    validator = ConfigValidator()

    # Test 1: Circular dependency
    print("Test 1: Circular dependency detection")
    config_str = """
name: circular_test
methods:
  - id: A
    type: dummy
  - id: B
    type: dummy
connections:
  - from: A
    to: B
  - from: B
    to: A
"""
    config = parser.parse_string(config_str)
    is_valid, errors = validator.validate(config)
    print(f"  Result: {errors[0] if errors else 'No errors'}")

    # Test 2: Invalid connection
    print("\nTest 2: Connection to non-existent method")
    config_str = """
name: bad_connection
methods:
  - id: A
    type: dummy
connections:
  - from: A
    to: B
"""
    config = parser.parse_string(config_str)
    is_valid, errors = validator.validate(config)
    print(f"  Result: {errors[0] if errors else 'No errors'}")

    # Test 3: Invalid pooling rule
    print("\nTest 3: Pooling rule without multiple inputs")
    config_str = """
name: bad_pooling
methods:
  - id: A
    type: dummy
  - id: B
    type: dummy
connections:
  - from: A
    to: B
pooling_rules:
  B:
    strategy: best
    metric: score
"""
    config = parser.parse_string(config_str)
    is_valid, errors = validator.validate(config)
    print(f"  Result: {errors[0] if errors else 'No errors'}")


def demo_full_workflow():
    """Demonstrate a complete workflow."""
    print_section("6. Complete Workflow Example")

    print("Creating a diamond pipeline for consensus docking...")

    # Create configuration
    config = create_diamond_pipeline(
        name="consensus_docking_workflow",
        preparation_method="ligand_preparation",
        parallel_methods=["vina_docking", "autodock_docking", "smina_docking"],
        pooling_method="consensus_filter",
        final_method="scoring_analysis",
        pooling_strategy="best",
        metric="docking_score",
        description="Three-method consensus docking with scoring"
    )

    # Validate
    validator = ConfigValidator()
    is_valid, errors = validator.validate(config)
    print(f"1. Configuration created and validated: {'✓' if is_valid else '✗'}")

    # Convert to pipeline
    pipeline = Pipeline.from_config(config)
    print(f"2. Converted to Pipeline object: ✓")

    # Validate pipeline
    is_valid, errors = pipeline.validate()
    print(f"3. Pipeline structure validated: {'✓' if is_valid else '✗'}")

    # Show structure
    print(f"\nPipeline Structure:")
    print(f"  - Name: {pipeline.name}")
    print(f"  - Tasks: {len(pipeline.tasks)}")
    print(f"  - Connections: {len(pipeline.connections)}")
    print(f"  - Pooling Rules: {len(pipeline.pooling_rules)}")

    print(f"\nExecution Flow:")
    print(f"  1. ligand_preparation")
    print(f"  2. [vina_docking, autodock_docking, smina_docking] (parallel)")
    print(f"  3. consensus_filter (pools results)")
    print(f"  4. scoring_analysis")

    print(f"\n✓ Ready for execution!")


def main():
    """Run all demonstrations."""
    print(f"\n{'*' * 70}")
    print(f"  Stage 2: Pipeline Configuration System Demo")
    print(f"{'*' * 70}")

    try:
        demo_parse_and_validate()
        demo_convert_to_pipeline()
        demo_templates()
        demo_variable_substitution()
        demo_error_handling()
        demo_full_workflow()

        print(f"\n{'*' * 70}")
        print(f"  All demonstrations completed successfully!")
        print(f"{'*' * 70}\n")

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
