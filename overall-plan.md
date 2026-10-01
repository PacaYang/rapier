# Virtual Screening Platform - Overall Plan

## Project Goal
Build a modular, SLURM-based virtual screening platform for molecule discovery that supports customizable pipelines with serial/parallel execution, robust dependency management, and comprehensive monitoring.

## Stages

### Stage 1: Core Architecture & Data Models
Design and implement the foundational class structure for the platform. This includes base classes for Methods (algorithms), Tasks (individual executions), Pipelines (workflow definitions), and the execution graph. Define the interfaces and contracts that all components will follow, including input/output specifications, environment requirements, and lifecycle hooks (pre-run validation, execution, post-run collection).

**Key Deliverables:**
- `Method` base class with lifecycle hooks
- `Task` class for tracking individual executions
- `Pipeline` class for workflow representation
- `ExecutionGraph` for DAG representation
- Data models for inputs, outputs, and environment specs

### Stage 2: Pipeline Configuration System
Build the system for users to define custom pipelines through configuration files (YAML/JSON). Implement parsing, validation, and internal representation of pipeline definitions. Support for defining methods, connections (serial/parallel), pooling rules, and failure handling policies. Create a configuration schema and validator.

**Key Deliverables:**
- YAML/JSON pipeline configuration format
- Configuration parser and validator
- Pipeline builder from configuration
- Schema documentation
- Example pipeline configurations

### Stage 3: SLURM Integration Layer
Implement the interface between the platform and SLURM. This includes reading SLURM specifications, generating job scripts, submitting jobs, monitoring queue status, and retrieving results. Handle SLURM-specific resource allocation (CPUs, memory, GPUs, partitions) and job dependencies.

**Key Deliverables:**
- SLURM job script generator
- Job submission and tracking system
- SLURM queue monitoring
- Resource allocation parser
- Job status polling and callbacks

### Stage 4: Dependency & Execution Engine
Build the core execution engine that manages the pipeline DAG, handles task dependencies, schedules tasks based on completion status, and implements failure handling policies. Support optional continuation when tasks fail, parallel execution of independent branches, and result pooling from multiple upstream tasks.

**Key Deliverables:**
- DAG-based task scheduler
- Dependency resolution system
- Failure handling policies (continue/stop/retry)
- Parallel execution manager
- Result pooling system

### Stage 5: Logging & Monitoring System
Implement a comprehensive, multi-level logging system that provides visibility into pipeline execution. Top-level dashboard showing overall progress, method-level logs for individual algorithm execution, SLURM job monitoring, and output file validation tracking. Support real-time updates and historical log retrieval.

**Key Deliverables:**
- Multi-level logging infrastructure (platform, pipeline, method, task)
- Real-time monitoring dashboard
- Log aggregation and querying
- Progress tracking and reporting
- Alert system for failures

### Stage 6: File Management & Project Structure
Design and implement the project folder structure that separates inputs, intermediate files, and outputs. Create utilities for automatic cleanup of intermediate files while preserving important results. Implement path management that guides code organization and makes it easy to locate files at any stage of execution.

**Key Deliverables:**
- Standard project directory structure
- Path management utilities
- Intermediate file tracking
- Cleanup policies and tools
- File lifecycle management

### Stage 7: Method Library & Examples
Create the base method classes for common virtual screening algorithms and implement concrete examples. Start with Vina docking (receptor preparation + docking steps), demonstrating multi-step method handling. Each method should specify its input format, output format, environment requirements (software dependencies, SLURM resources), and implement validation logic.

**Key Deliverables:**
- Base method class implementations
- Vina docking method (2-step: prep + dock)
- Example methods (2-3 additional screening algorithms)
- Method registry system
- Method documentation templates

### Stage 8: Testing & Documentation
Develop comprehensive tests covering unit tests for core classes, integration tests for SLURM interaction, and end-to-end pipeline tests. Write user documentation including getting started guide, pipeline configuration reference, method development guide, and troubleshooting. Create example pipelines demonstrating common screening workflows.

**Key Deliverables:**
- Unit test suite
- Integration test suite
- End-to-end pipeline tests
- User documentation
- Developer documentation
- Example pipeline gallery

## Dependencies

**Sequential dependencies:**
- Stage 1 must complete before all others (foundation)
- Stage 2 depends on Stage 1 (uses core classes)
- Stage 3 depends on Stage 1 (implements Method execution interface)
- Stage 4 depends on Stages 1, 2, 3 (orchestrates everything)
- Stage 5 can start after Stage 1, runs parallel with Stages 2-4
- Stage 6 depends on Stage 1, can run parallel with Stages 2-4
- Stage 7 depends on Stages 1, 3, 6 (needs Method base, SLURM, and file structure)
- Stage 8 depends on all previous stages (tests the complete system)

**Execution order:**
1. Stage 1: Core Architecture
2. Stages 2, 3, 5, 6 can run in parallel (after Stage 1)
3. Stage 4: Execution Engine (after Stages 2, 3 complete)
4. Stage 7: Method Library (after Stages 1, 3, 6 complete)
5. Stage 8: Testing & Documentation (after all others)

## Success Criteria

The project is complete when:
1. ✅ Users can define custom pipelines via YAML configuration
2. ✅ Pipelines execute on SLURM with proper resource allocation
3. ✅ Pipeline supports serial and parallel task execution
4. ✅ Dependency system allows optional continuation on failures
5. ✅ Real-time monitoring dashboard shows execution progress
6. ✅ File structure cleanly separates inputs, intermediates, and outputs
7. ✅ At least one complete multi-step method (Vina docking) is implemented and tested
8. ✅ Documentation allows new users to create and run their first pipeline
9. ✅ Test suite validates all core functionality
10. ✅ Example pipelines demonstrate common virtual screening workflows

## Technical Stack Recommendations

- **Language:** Python 3.8+ (scientific computing ecosystem, SLURM integration libraries)
- **Configuration:** YAML (human-readable, supports complex structures)
- **Logging:** Python logging + structured logs (JSON for parsing)
- **DAG Management:** NetworkX (graph operations, topological sorting)
- **SLURM Integration:** subprocess + Slurm REST API or pyslurm
- **Testing:** pytest
- **Documentation:** Markdown + Sphinx (API docs)

## Project Structure Preview

```
rapier/
├── rapier/                      # Main package
│   ├── core/                    # Stage 1: Core classes
│   ├── config/                  # Stage 2: Configuration system
│   ├── slurm/                   # Stage 3: SLURM integration
│   ├── engine/                  # Stage 4: Execution engine
│   ├── logging/                 # Stage 5: Logging system
│   ├── storage/                 # Stage 6: File management
│   └── methods/                 # Stage 7: Method library
├── tests/                       # Stage 8: Test suite
├── docs/                        # Stage 8: Documentation
├── examples/                    # Example pipelines
│   └── pipelines/
├── pyproject.toml              # Package configuration
└── README.md
```

## Next Steps

After approval of this overall plan, I will create detailed file-by-file plans for each stage, specifying exactly what will be implemented in each file, the implementation order, and verification steps.
