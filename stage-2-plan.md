# Stage 2: Pipeline Configuration System

## Goal
Build the system for users to define custom pipelines through YAML configuration files. Implement parsing, validation, and internal representation of pipeline definitions. Support for defining methods, connections (serial/parallel), pooling rules, and failure handling policies.

## Files to Create

### `rapier/config/__init__.py`
- Configuration module initialization
- Export main classes: `PipelineConfig`, `ConfigParser`, `ConfigValidator`

### `rapier/config/schema.py`
- YAML schema definitions using JSON Schema or custom validators
- Schema for pipeline configuration structure
- Schema for method definitions, connections, pooling rules
- Validation rules for configuration fields

### `rapier/config/parser.py`
- `ConfigParser` class
- Methods: `parse_file()`, `parse_string()`, `parse_dict()`
- Convert YAML/JSON to internal Python objects
- Handle includes and references between config files
- Support for variable substitution (e.g., `${PROJECT_DIR}`)

### `rapier/config/validator.py`
- `ConfigValidator` class
- Methods: `validate_structure()`, `validate_connections()`, `validate_methods()`
- Check for circular dependencies
- Verify all referenced methods exist
- Validate pooling rules syntax
- Check resource specifications are valid

### `rapier/config/pipeline_config.py`
- `PipelineConfig` dataclass
- Represents parsed and validated configuration
- Properties: `name`, `description`, `methods`, `connections`, `pooling_rules`, `failure_policies`
- Method: `to_pipeline()` - convert to `Pipeline` object

### `rapier/config/method_config.py`
- `MethodConfig` dataclass
- Properties: `id`, `type`, `parameters`, `resources`, `failure_policy`
- Validation of method-specific parameters
- Resource requirement specifications

### `rapier/config/templates.py`
- Template configuration generators
- `create_simple_pipeline()` - single serial chain
- `create_parallel_pipeline()` - multiple parallel branches
- `create_pooling_pipeline()` - branches with pooling
- Helper for users to get started quickly

### `examples/pipelines/simple_serial.yaml`
- Example: A -> B -> C serial pipeline
- Demonstrates basic method chaining
- Comments explaining each section

### `examples/pipelines/parallel_pooling.yaml`
- Example matching user's requirement: A,D -> Pooling -> C
- Demonstrates parallel execution and result pooling
- Shows pooling rule syntax

### `examples/pipelines/multi_step_method.yaml`
- Example with Vina docking (prep + dock steps)
- Demonstrates multi-step method configuration
- Shows how intermediate outputs are handled

### `docs/config_reference.md`
- Complete configuration reference documentation
- All available fields and their meanings
- Pooling rule syntax guide
- Failure policy options
- Resource specification format

## Files to Modify

### `rapier/core/pipeline.py`
- Add class method: `from_config(config: PipelineConfig) -> Pipeline`
- Convert PipelineConfig to Pipeline instance

## Implementation Order

1. Define schema (`schema.py`) - establishes structure
2. Create dataclasses (`method_config.py`, `pipeline_config.py`) - internal representation
3. Implement parser (`parser.py`) - reads YAML into dataclasses
4. Implement validator (`validator.py`) - validates parsed config
5. Create templates (`templates.py`) - helper functions
6. Write example configurations (`.yaml` files)
7. Write documentation (`config_reference.md`)
8. Add `from_config()` method to `Pipeline` class

## Verification Steps

```bash
# Test parsing example configurations
python3 -c "
from rapier.config import ConfigParser
config = ConfigParser.parse_file('examples/pipelines/simple_serial.yaml')
print(f'Parsed pipeline: {config.name}')
print(f'Methods: {len(config.methods)}')
"

# Test validation
python3 -c "
from rapier.config import ConfigParser, ConfigValidator
config = ConfigParser.parse_file('examples/pipelines/parallel_pooling.yaml')
validator = ConfigValidator()
is_valid, errors = validator.validate(config)
print(f'Valid: {is_valid}')
if errors:
    for error in errors:
        print(f'  - {error}')
"

# Test conversion to Pipeline
python3 -c "
from rapier.config import ConfigParser
from rapier.core import Pipeline
config = ConfigParser.parse_file('examples/pipelines/simple_serial.yaml')
pipeline = Pipeline.from_config(config)
print(f'Pipeline has {len(pipeline.tasks)} tasks')
"

# Test invalid configuration handling
python3 -c "
from rapier.config import ConfigParser, ConfigValidator
# Create config with circular dependency
config_str = '''
name: circular
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
'''
config = ConfigParser.parse_string(config_str)
validator = ConfigValidator()
is_valid, errors = validator.validate(config)
assert not is_valid, 'Should detect circular dependency'
print('✓ Circular dependency detected')
"
```

## Key Decisions

**1. YAML as Primary Format**
- Human-readable and writable
- Good support for comments and documentation
- Standard in scientific computing workflows
- JSON also supported (subset of YAML)

**2. Declarative Configuration Style**
- Users declare what they want, not how to achieve it
- Platform determines execution order from dependencies
- Reduces configuration complexity

**3. Pooling Rule Syntax**
- Simple string-based rules: `"any"`, `"all"`, `"majority"`, `"best[metric]"`
- Extensible for custom pooling functions
- Example: `pooling: "best[docking_score]"` takes molecule with best score

**4. Failure Policy Levels**
- Global pipeline default
- Per-method override
- Policies: `stop` (halt entire pipeline), `continue` (skip downstream), `retry[N]` (retry N times)

**5. Variable Substitution**
- Support environment variables: `${VAR_NAME}`
- Support project-relative paths: `${PROJECT_DIR}/data/input.sdf`
- Makes configurations portable across environments
