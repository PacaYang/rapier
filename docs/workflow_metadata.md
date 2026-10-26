# Workflow metadata and resource controllers

A workflow has a metadata file that describes its working directory and compute
location (`region`).
Each method has one resource controller that resolves the computing resources
needed for that method. Rapier also records the intended relationship between
these workflow settings and task output directories as a placeholder.

## Create a workflow and its metadata file

Keep the workflow definition and companion metadata file together:

```text
screening.yaml
screening.meta.yaml
```

Reference the companion file from the workflow definition:

```yaml
name: screening
metadata_file: screening.meta.yaml
methods:
  # Method definitions and connections go here.
```

The companion file contains these values:

```yaml
working_dir: ../../runs/screening
region: local
```

`working_dir` is the root directory associated with this workflow. A relative
value is resolved against the directory containing the metadata file.
`metadata_file` itself is resolved relative to the workflow file. This lets a
workflow refer to metadata in another directory while keeping that metadata's
working directory relative to its own location.

`region` is a nonempty compute location or cluster identifier. Use the name
understood by the environment where the workflow will run, such as `us-east-1`,
`gpu-cluster-a`, or `local`. The value is recorded with the workflow and used
in the output-directory placeholder; backend selection and scheduler routing
are reserved for a future implementation.

The complete example is
[13_workflow_metadata.yaml](../examples/pipelines/13_workflow_metadata.yaml),
with its companion
[13_workflow_metadata.meta.yaml](../examples/pipelines/13_workflow_metadata.meta.yaml).
Validate the configuration and build its pipeline with:

```bash
python scripts/validate_config.py examples/pipelines/13_workflow_metadata.yaml --build
```

The example uses the built-in `molecule_filter` method, which runs in Python
without RDKit, Vina, or a prediction service. Validation and construction do not
execute the method or submit a job.

For programmatically generated configurations, the same settings can be supplied
as an inline mapping:

```yaml
metadata:
  working_dir: ./runs/screening
  region: local
```

When parsing an inline configuration, relative working directories use the
configuration's project directory. Existing workflows that omit metadata remain
loadable: their defaults are `working_dir: .` and `region: default`. Add an
explicit companion file when you want those settings to travel with a workflow.

## Give each method its resource controller

Declare resource overrides in a single `resource_controller` mapping on the
method:

```yaml
methods:
  - id: filter
    type: molecule_filter
    inputs:
      molecules: pipeline.molecules
    resource_controller:
      cpus: 2
      memory: 2GB
      time_limit: "00:10:00"
```

When the pipeline is built, Rapier creates one resource-controller object for
each method task. It starts with the method's `ResourceSpec` defaults and applies
the supplied overrides. A method with no resource overrides still receives its
own controller. Controllers belong to individual methods, so changing one
method's resources does not change another method's resources.

The controller supports these computing-resource fields:

| Field | Meaning |
| --- | --- |
| `cpus` | Number of CPUs allocated to the method. |
| `memory` | Memory request, such as `2GB` or `8000MB`. |
| `gpus` | Number of GPUs; use `0` for CPU execution. |
| `gpu_type` | GPU model or scheduler GPU type, when applicable. |
| `time_limit` | Maximum execution time. |
| `partition` | Scheduler partition, when applicable. |
| `nodes` | Number of compute nodes. |
| `account`, `qos`, `constraint` | Optional scheduler request settings. |
| `exclusive` | Whether to request exclusive use of allocated nodes. |

The method's `ResourceSpec.time` default becomes the controller's `time_limit`.
Resolved values remain available in task resource metadata for the existing
execution and scheduling code.

Older workflows can continue using `resources` as the resource mapping. Use only
one of `resource_controller` or `resources` on a method; declaring both is
ambiguous and is rejected. New workflow exports use `resource_controller`.

## Task output directories are a placeholder

Rapier records this intended task output-directory template:

```text
{working_dir}/{region}/{task_id}/outputs
```

The workflow and its tasks expose the template with a `placeholder` status. It
describes how the workflow working directory and compute region will relate to
each task's output directory. This release does not create these directories or route
task outputs through this template; execution continues to use the existing task
directory handling.

For the supplied example, the template represents an intended location like:

```text
<repository>/runs/workflow_metadata/local/filter/outputs
```

You can inspect the resolved workflow settings, method resources, and output
placeholder after building the pipeline:

```python
from rapier.config import ConfigParser
from rapier.core.pipeline import Pipeline

config = ConfigParser().parse_file("examples/pipelines/13_workflow_metadata.yaml")
pipeline = Pipeline.from_config(config)
task = pipeline.tasks["filter"]

print(pipeline.workflow_metadata.resolved_working_dir)
print(task.resource_controller.to_dict())
print(task.metadata["output_directory"])
```

## Use the workflow builder

Select the workflow settings to edit its working directory and compute region
or cluster. Each
method's inspector exposes its attached resource controller. The output-directory
template is shown as a placeholder.

Export the workflow YAML and its companion metadata YAML, and keep the files
together. To import that workflow, select both files in the import picker so the
builder can restore its metadata as well as its graph.
