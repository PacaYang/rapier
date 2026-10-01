# Pipeline Configuration Reference

Complete reference for Rapier pipeline configuration files (YAML/JSON).

## Table of Contents

1. [Basic Structure](#basic-structure)
2. [Pipeline Fields](#pipeline-fields)
3. [Method Configuration](#method-configuration)
4. [Connections](#connections)
5. [Pooling Rules](#pooling-rules)
6. [Failure Policies](#failure-policies)
7. [Resource Specification](#resource-specification)
8. [Variable Substitution](#variable-substitution)
9. [Complete Examples](#complete-examples)

## Basic Structure

A pipeline configuration file has the following top-level structure:

```yaml
name: pipeline_name
description: Optional pipeline description
version: 1.0.0

methods:
  - id: method_1
    type: method_type
    # ... method configuration

connections:
  - from: method_1
    to: method_2
    type: serial

pooling_rules:
  method_id:
    strategy: best
    metric: score

failure_policy: stop

environment:
  variables:
    VAR_NAME: value
```

## Pipeline Fields

### Required Fields

- **`name`** (string): Unique pipeline name. Must be non-empty.
  ```yaml
  name: my_screening_pipeline
  ```

- **`methods`** (array): List of method configurations. Must have at least one method.
  ```yaml
  methods:
    - id: step1
      type: filtering
    - id: step2
      type: docking
  ```

### Optional Fields

- **`description`** (string): Human-readable pipeline description.
  ```yaml
  description: Multi-step virtual screening with consensus docking
  ```

- **`version`** (string): Configuration version (semantic versioning format).
  ```yaml
  version: 1.0.0
  ```

- **`connections`** (array): List of connections between methods. If omitted, methods run independently.
  ```yaml
  connections:
    - from: step1
      to: step2
  ```

- **`pooling_rules`** (object): Rules for pooling results from parallel branches.
  ```yaml
  pooling_rules:
    consensus_step:
      strategy: best
      metric: docking_score
  ```

- **`failure_policy`** (string): Global failure handling policy. Default: `stop`.
  - Values: `stop`, `continue`, `retry`
  ```yaml
  failure_policy: continue
  ```

- **`environment`** (object): Environment settings and variables.
  ```yaml
  environment:
    variables:
      RECEPTOR: protein_kinase
    project_dir: /path/to/project
  ```

## Method Configuration

Each method in the `methods` array has the following structure:

### Required Method Fields

- **`id`** (string): Unique method identifier. Must start with a letter, can contain letters, numbers, underscores, and hyphens.
  ```yaml
  id: vina_docking_1
  ```

- **`type`** (string): Method type identifier (e.g., `vina_dock`, `filtering`, `scoring`).
  ```yaml
  type: vina_dock
  ```

### Optional Method Fields

- **`description`** (string): Method description.
  ```yaml
  description: AutoDock Vina molecular docking with exhaustive search
  ```

- **`config`** (object): Strict method-specific settings. Structure depends on method type.
  ```yaml
  config:
    receptor: /path/to/receptor.pdbqt
    exhaustiveness: 8
    num_modes: 10
  ```

- **`resources`** (object): Resource requirements for SLURM execution. See [Resource Specification](#resource-specification).
  ```yaml
  resources:
    cpus: 8
    memory: 16GB
    gpus: 0
    time_limit: 2h
    partition: standard
  ```

- **`failure_policy`** (string): Method-level failure policy. Overrides global policy.
  - Values: `stop`, `continue`, `retry`
  ```yaml
  failure_policy: retry
  ```

- **`retry_count`** (integer): Number of retry attempts if `failure_policy` is `retry`. Range: 1-10.
  ```yaml
  retry_count: 3
  ```

### Complete Method Example

```yaml
- id: vina_docking
  type: vina_dock
  description: AutoDock Vina molecular docking
  config:
    receptor: ${PROJECT_DIR}/data/receptor.pdbqt
    box_center: [10.0, 15.0, 20.0]
    box_size: [20.0, 20.0, 20.0]
    exhaustiveness: 16
  resources:
    cpus: 8
    memory: 16GB
    time_limit: 3h
    partition: compute
  failure_policy: retry
  retry_count: 2
```

## Connections

Connections define the execution order and dependencies between methods.

### Connection Fields

- **`from`** (string or array): Source method ID(s).
  - Single source (serial): `from: method_a`
  - Multiple sources (parallel): `from: [method_a, method_b, method_c]`

- **`to`** (string): Target method ID.

- **`type`** (string, optional): Connection type. Default: `serial`.
  - `serial`: Sequential execution
  - `parallel`: Concurrent execution

### Connection Examples

**Serial connection** (A → B):
```yaml
connections:
  - from: method_a
    to: method_b
    type: serial
```

**Parallel connections** (A → B, A → C):
```yaml
connections:
  - from: method_a
    to: method_b
    type: parallel
  - from: method_a
    to: method_c
    type: parallel
```

**Converging connections** ([A, B] → C):
```yaml
connections:
  - from: [method_a, method_b]
    to: method_c
    type: parallel
```

## Pooling Rules

Pooling rules define how to combine results from multiple upstream methods.

### When to Use Pooling

Use pooling rules when a method has multiple upstream connections. The pooling rule determines which results to pass to the downstream method.

### Pooling Rule Fields

- **`strategy`** (string, required): Pooling strategy.
  - `any`: Accept if any upstream method succeeds
  - `all`: Require all upstream methods to succeed
  - `majority`: Require majority of upstream methods to succeed
  - `best`: Select best result based on a metric
  - `first`: Use the first completed result
  - `custom`: Use a custom pooling function

- **`metric`** (string): Metric name for `best` strategy. Required for `best`.

- **`minimize`** (boolean): Whether to minimize the metric. Default: `false` (maximize).

- **`function`** (string): Custom pooling function name for `custom` strategy.

### Pooling Examples

**Accept any result**:
```yaml
pooling_rules:
  consensus_step:
    strategy: any
```

**Require all results**:
```yaml
pooling_rules:
  consensus_step:
    strategy: all
```

**Select best by metric**:
```yaml
pooling_rules:
  consensus_step:
    strategy: best
    metric: docking_score
    minimize: true  # Lower score is better
```

**Majority voting**:
```yaml
pooling_rules:
  consensus_step:
    strategy: majority
```

## Failure Policies

Failure policies control what happens when a method fails.

### Policy Types

- **`stop`** (default): Halt the entire pipeline immediately.
- **`continue`**: Skip the failed method and continue with downstream methods if possible.
- **`retry`**: Retry the method up to `retry_count` times before failing.

### Policy Levels

1. **Global** (pipeline-level): Default policy for all methods.
   ```yaml
   failure_policy: continue
   ```

2. **Method-level**: Overrides global policy for specific methods.
   ```yaml
   methods:
     - id: critical_step
       type: docking
       failure_policy: stop
     - id: optional_step
       type: analysis
       failure_policy: continue
   ```

### Retry Configuration

When using `retry` policy, specify the number of retry attempts:

```yaml
- id: unstable_method
  type: external_api_call
  failure_policy: retry
  retry_count: 3  # Try up to 3 times
```

## Resource Specification

Resource specifications define SLURM resource requirements for each method.

### Resource Fields

- **`cpus`** (integer): Number of CPU cores. Minimum: 1.
  ```yaml
  cpus: 8
  ```

- **`memory`** (string): Memory requirement with unit (`MB`, `GB`, `TB`).
  ```yaml
  memory: 16GB
  ```

- **`gpus`** (integer): Number of GPUs. Minimum: 0.
  ```yaml
  gpus: 1
  ```

- **`time_limit`** (string): Maximum execution time with unit (`s`, `m`, `h`, `d`).
  ```yaml
  time_limit: 2h
  ```

- **`partition`** (string): SLURM partition name.
  ```yaml
  partition: gpu
  ```

### Resource Examples

**CPU-intensive task**:
```yaml
resources:
  cpus: 32
  memory: 64GB
  time_limit: 6h
  partition: compute
```

**GPU task**:
```yaml
resources:
  cpus: 4
  memory: 16GB
  gpus: 2
  time_limit: 1h
  partition: gpu
```

**Quick task**:
```yaml
resources:
  cpus: 1
  memory: 2GB
  time_limit: 15m
```

## Variable Substitution

Variables can be used throughout the configuration using `${VAR_NAME}` syntax.

### Variable Types

1. **Project directory**: Automatically set to the configuration file's directory.
   ```yaml
   receptor: ${PROJECT_DIR}/data/receptor.pdb
   ```

2. **Custom variables**: Defined in the `environment.variables` section.
   ```yaml
   environment:
     variables:
       RECEPTOR_NAME: kinase_target
       DATA_PATH: /shared/data

   # Usage:
   config:
     receptor: ${DATA_PATH}/${RECEPTOR_NAME}.pdb
   ```

3. **Environment variables**: System environment variables are automatically available.
   ```yaml
   output_dir: ${HOME}/screening_results
   ```

### Variable Example

```yaml
environment:
  variables:
    TARGET: protein_kinase
    DATASET: chembl_kinase_inhibitors

methods:
  - id: docking
    type: vina_dock
    config:
      receptor: ${PROJECT_DIR}/receptors/${TARGET}.pdbqt
      ligands: ${PROJECT_DIR}/datasets/${DATASET}.sdf
      output: ${PROJECT_DIR}/results/${TARGET}_${DATASET}/
```

## Complete Examples

### Example 1: Simple Serial Pipeline

```yaml
name: basic_screening
description: Basic virtual screening workflow
version: 1.0.0

methods:
  - id: filter
    type: filtering
    config:
      rules: [lipinski, veber]
    resources:
      cpus: 2
      memory: 4GB
      time_limit: 30m

  - id: dock
    type: vina_dock
    config:
      receptor: ${PROJECT_DIR}/receptor.pdbqt
      exhaustiveness: 8
    resources:
      cpus: 8
      memory: 8GB
      time_limit: 2h

connections:
  - from: filter
    to: dock
```

### Example 2: Parallel with Pooling

```yaml
name: consensus_docking
description: Multi-method consensus docking
version: 1.0.0

failure_policy: continue

methods:
  - id: vina
    type: vina_dock
    resources:
      cpus: 8
      memory: 8GB

  - id: smina
    type: smina_dock
    resources:
      cpus: 8
      memory: 8GB

  - id: consensus
    type: consensus_filter
    resources:
      cpus: 4
      memory: 4GB

connections:
  - from: [vina, smina]
    to: consensus
    type: parallel

pooling_rules:
  consensus:
    strategy: best
    metric: consensus_score
```

### Example 3: Diamond Pipeline

```yaml
name: full_screening
description: Complete screening with preparation and analysis
version: 1.0.0

methods:
  - id: prep
    type: ligand_prep

  - id: vina
    type: vina_dock

  - id: autodock
    type: autodock

  - id: smina
    type: smina_dock

  - id: pool
    type: consensus_filter

  - id: analyze
    type: analysis

connections:
  - from: prep
    to: vina
  - from: prep
    to: autodock
  - from: prep
    to: smina
  - from: [vina, autodock, smina]
    to: pool
  - from: pool
    to: analyze

pooling_rules:
  pool:
    strategy: majority
```

## Validation

Before running a pipeline, validate the configuration:

```python
from rapier.config import ConfigParser, ConfigValidator

# Parse configuration
parser = ConfigParser()
config = parser.parse_file('pipeline.yaml')

# Validate
validator = ConfigValidator()
is_valid, errors = validator.validate(config)

if not is_valid:
    for error in errors:
        print(f"Error: {error}")
```

## Best Practices

1. **Use descriptive IDs**: Make method IDs clear and meaningful.
   ```yaml
   id: vina_exhaustive_16  # Good
   id: step1               # Less clear
   ```

2. **Add descriptions**: Document what each method does.
   ```yaml
   description: Filter molecules using Lipinski's Rule of Five
   ```

3. **Use variables**: Make configurations portable.
   ```yaml
   receptor: ${PROJECT_DIR}/data/receptor.pdb  # Portable
   receptor: /home/user/project/data/receptor.pdb  # Hard-coded
   ```

4. **Set appropriate failure policies**: Use `continue` for non-critical steps.
   ```yaml
   - id: visualization
     type: plot_results
     failure_policy: continue  # Don't fail pipeline if plotting fails
   ```

5. **Specify resources carefully**: Match resources to computational needs.
   ```yaml
   resources:
     cpus: 8        # Match to method's parallelization capability
     memory: 16GB   # Provide adequate memory
     time_limit: 2h # Allow sufficient time with buffer
   ```

6. **Version your configurations**: Track changes to pipeline definitions.
   ```yaml
   version: 1.2.0
   ```
