# Stage 2: Pipeline Configuration System

## Quick Start

### Parse and Validate a Configuration

```python
from rapier.config import ConfigParser, ConfigValidator

# Parse YAML configuration
parser = ConfigParser()
config = parser.parse_file('examples/pipelines/simple_serial.yaml')

# Validate configuration
validator = ConfigValidator()
is_valid, errors = validator.validate(config)

if is_valid:
    print("Configuration is valid!")
else:
    for error in errors:
        print(f"Error: {error}")
```

### Create a Pipeline from Configuration

```python
from rapier.config import ConfigParser
from rapier.core import Pipeline

# Parse configuration
config = ConfigParser().parse_file('pipeline.yaml')

# Convert to Pipeline object
pipeline = Pipeline.from_config(config)

print(f"Pipeline: {pipeline.name}")
print(f"Tasks: {len(pipeline.tasks)}")
print(f"Connections: {len(pipeline.connections)}")
```

### Generate Pipeline from Template

```python
from rapier.config import create_diamond_pipeline

# Create a diamond pipeline: prep -> [parallel methods] -> pool -> final
config = create_diamond_pipeline(
    name="consensus_docking",
    preparation_method="ligand_prep",
    parallel_methods=["vina", "autodock", "smina"],
    pooling_method="consensus",
    final_method="scoring",
    pooling_strategy="majority"
)

# Convert to pipeline
from rapier.core import Pipeline
pipeline = Pipeline.from_config(config)
```

## Configuration File Format

### Basic Structure

```yaml
name: my_pipeline
description: Pipeline description
version: 1.0.0

methods:
  - id: method_1
    type: method_type
    parameters:
      param1: value1
    resources:
      cpus: 8
      memory: 16GB
      time_limit: 2h

connections:
  - from: method_1
    to: method_2

pooling_rules:
  method_id:
    strategy: best
    metric: score

failure_policy: stop
```

### Example: Parallel Docking with Pooling

```yaml
name: consensus_docking
description: Run multiple docking methods and pool results

methods:
  - id: vina
    type: vina_dock
    parameters:
      receptor: ${PROJECT_DIR}/receptor.pdbqt
      exhaustiveness: 8
    resources:
      cpus: 8
      memory: 8GB
      time_limit: 2h

  - id: autodock
    type: autodock
    parameters:
      receptor: ${PROJECT_DIR}/receptor.pdbqt
    resources:
      cpus: 8
      memory: 8GB
      time_limit: 2h

  - id: consensus
    type: consensus_filter
    resources:
      cpus: 4
      memory: 4GB

connections:
  - from: [vina, autodock]
    to: consensus
    type: parallel

pooling_rules:
  consensus:
    strategy: best
    metric: docking_score
    minimize: true

failure_policy: continue
```

## Template Functions

### Simple Serial Pipeline

```python
from rapier.config import create_simple_pipeline

config = create_simple_pipeline(
    "screening",
    ["filtering", "docking", "scoring"]
)
```

### Parallel Pipeline

```python
from rapier.config import create_parallel_pipeline

config = create_parallel_pipeline(
    "multi_docking",
    initial_method="prep",
    parallel_methods=["vina", "autodock", "smina"],
    final_method="consensus"
)
```

### Pooling Pipeline

```python
from rapier.config import create_pooling_pipeline

config = create_pooling_pipeline(
    "consensus",
    initial_methods=["method_a", "method_b"],
    pooling_method="consensus",
    pooling_strategy="best",
    metric="score"
)
```

### Diamond Pipeline

```python
from rapier.config import create_diamond_pipeline

config = create_diamond_pipeline(
    "full_workflow",
    preparation_method="prep",
    parallel_methods=["vina", "autodock"],
    pooling_method="consensus",
    final_method="analysis",
    pooling_strategy="majority"
)
```

## Variable Substitution

Use `${VAR_NAME}` syntax for variable substitution:

```yaml
environment:
  variables:
    TARGET: protein_kinase
    DATA_PATH: /shared/data

methods:
  - id: docking
    type: vina_dock
    parameters:
      receptor: ${DATA_PATH}/${TARGET}.pdbqt
      output: ${PROJECT_DIR}/results/
```

Supported variables:
- `${PROJECT_DIR}`: Automatically set to config file directory
- `${ENV_VAR}`: Any environment variable
- Custom variables defined in `environment.variables`

## Pooling Strategies

- **`any`**: Accept if any upstream method succeeds
- **`all`**: Require all upstream methods to succeed
- **`majority`**: Require majority of methods to succeed
- **`best`**: Select best result based on a metric
- **`first`**: Use first completed result
- **`custom`**: Use a custom pooling function

Example:

```yaml
pooling_rules:
  consensus:
    strategy: best
    metric: docking_score
    minimize: true  # Lower is better
```

## Failure Policies

- **`stop`** (default): Halt entire pipeline on failure
- **`continue`**: Skip failed method and continue
- **`retry`**: Retry method up to `retry_count` times

Example:

```yaml
failure_policy: continue  # Global default

methods:
  - id: critical_step
    type: docking
    failure_policy: stop  # Override for this method
    
  - id: optional_viz
    type: visualization
    failure_policy: continue
    
  - id: flaky_api
    type: api_call
    failure_policy: retry
    retry_count: 3
```

## Resource Specifications

```yaml
resources:
  cpus: 8              # Number of CPU cores
  memory: 16GB         # Memory (MB, GB, TB)
  gpus: 1              # Number of GPUs
  time_limit: 2h       # Time limit (s, m, h, d)
  partition: compute   # SLURM partition name
```

## Examples

Three complete example pipelines are provided:

1. **`examples/pipelines/simple_serial.yaml`**
   - Basic serial workflow: filtering → docking → scoring
   - Demonstrates resource specifications
   - Shows variable substitution

2. **`examples/pipelines/parallel_pooling.yaml`**
   - Parallel docking with consensus pooling
   - Two docking methods run in parallel
   - Results pooled with "best" strategy

3. **`examples/pipelines/multi_step_method.yaml`**
   - Complete Vina workflow with preparation
   - Multi-step method configuration
   - Shows retry policies

## Documentation

Complete reference documentation is available in `docs/config_reference.md`.

## Demo Script

Run the demo script to see all features in action:

```bash
python3 demo_stage2.py
```

This demonstrates:
- Parsing and validation
- Converting to Pipeline objects
- Template generation
- Variable substitution
- Error detection
- Complete workflow example

## Testing

Run verification tests:

```bash
# Test parsing
python3 -c "
from rapier.config import ConfigParser
config = ConfigParser().parse_file('examples/pipelines/simple_serial.yaml')
print(f'Parsed: {config.name} with {len(config.methods)} methods')
"

# Test validation
python3 -c "
from rapier.config import ConfigParser, ConfigValidator
config = ConfigParser().parse_file('examples/pipelines/parallel_pooling.yaml')
is_valid, errors = ConfigValidator().validate(config)
print(f'Valid: {is_valid}')
"

# Test conversion
python3 -c "
from rapier.config import ConfigParser
from rapier.core import Pipeline
config = ConfigParser().parse_file('examples/pipelines/simple_serial.yaml')
pipeline = Pipeline.from_config(config)
print(f'Pipeline has {len(pipeline.tasks)} tasks')
"
```

## Next Steps

With Stage 2 complete, the system can now:
- ✅ Parse YAML/JSON pipeline configurations
- ✅ Validate structure and dependencies
- ✅ Convert to Pipeline objects
- ✅ Generate pipelines from templates
- ✅ Substitute variables
- ✅ Handle errors gracefully

Stage 3 will add SLURM integration to actually execute these pipelines on a cluster.
