# Stage 6: File Management & Project Structure - Implementation Summary

## Status: ✅ COMPLETED

Stage 6 has been successfully implemented with all components working as designed.

## Files Created

### Core Storage Components
1. **`rapier/storage/__init__.py`** - Module initialization and exports
2. **`rapier/storage/structure.py`** - ProjectStructure class for directory layout
3. **`rapier/storage/path_manager.py`** - PathManager for consistent path resolution
4. **`rapier/storage/metadata.py`** - FileMetadata for provenance tracking
5. **`rapier/storage/file_tracker.py`** - FileTracker for registering all files
6. **`rapier/storage/validators.py`** - File format validators (SDF, PDB, CSV, JSON)
7. **`rapier/storage/file_organizer.py`** - FileOrganizer for staging/collecting files
8. **`rapier/storage/cleanup_manager.py`** - CleanupManager with policies
9. **`rapier/storage/workspace.py`** - Workspace high-level interface

### Command-Line Tools
10. **`scripts/cleanup.py`** - Interactive cleanup utility
11. **`scripts/disk_usage.py`** - Disk usage analysis tool

### Testing
12. **`test_stage6_storage.py`** - Comprehensive integration test

## Features Implemented

### ✅ Project Directory Structure
```
project_root/
├── config/              # Pipeline configurations
├── inputs/              # Original input files (immutable)
│   ├── receptors/
│   └── ligands/
├── work/                # Working directory for executions
│   └── run_YYYYMMDD_HHMMSS/
│       ├── tasks/       # Task-specific directories
│       ├── intermediate/
│       ├── logs/
│       └── state.json
├── results/             # Final curated outputs
├── archive/             # Archived/compressed runs
```

### ✅ Smart File Staging
- **Symlinks for large files** (>100MB) to save disk space
- **Copy small files** for safety and independence
- **Immutable inputs** are always symlinked
- **Modified files** are always copied
- Automatic detection based on file size and location

### ✅ File Tracking & Metadata
- Track every file created during execution
- Categories: input, intermediate, output, log, script, checkpoint
- Metadata: size, timestamps, checksums (MD5/SHA256), source task
- Mark files as important to protect from cleanup
- Save/load manifest for persistence

### ✅ File Organization
- **Stage inputs**: Prepare task inputs with symlinks or copies
- **Collect outputs**: Gather and validate task outputs
- **Promote to results**: Move important files to results directory
- Validation of file existence and permissions

### ✅ Cleanup Policies
Three predefined strategies:

1. **minimal**: Keep everything (development/debugging)
   - Keep: inputs, outputs, logs, intermediates, scripts
   
2. **standard**: Remove intermediates (default)
   - Keep: inputs, outputs, logs
   - Remove: intermediates, scripts
   
3. **aggressive**: Keep only final results
   - Keep: inputs, outputs
   - Remove: logs, intermediates, scripts

### ✅ File Validators
Format-specific validation for:
- **SDF files**: Check for molecule blocks ($$$$, M END)
- **PDB files**: Check for ATOM/HETATM records
- **CSV files**: Validate header and expected columns
- **JSON files**: Validate JSON syntax
- **General**: File size, readability, emptiness

### ✅ Disk Usage Analysis
- Track disk usage by category (input/output/intermediate/log)
- Track disk usage by task
- Identify largest files
- Estimate space savings for each cleanup strategy
- Suggest cleanup targets

### ✅ Archive Support
- Compress entire runs to .tar.gz, .tar.bz2, or .tar.xz
- Optional cleanup after archiving
- Preserve run history in compressed format

## Verification Results

All verification tests from `stage-6-plan.md` passed successfully:

```
✓ Project structure creation works
✓ Path manager works
✓ File organizer staging works
✓ File tracker works
✓ Cleanup manager marking works
✓ File validation works
✓ Workspace integration works
```

## Comprehensive Test Results

The integration test (`test_stage6_storage.py`) demonstrated complete workflow:

- **Project Setup**: Created standard directory structure
- **Input Staging**: Validated and staged input files with symlinks
- **Task Execution**: Simulated 2 tasks with multiple outputs
- **File Tracking**: Tracked 6 files across 2 tasks
- **Output Collection**: Collected and validated outputs
- **Results Promotion**: Promoted 2 important files to results
- **Cleanup**: Tested dry-run and actual cleanup
- **State Persistence**: Saved and validated file manifest
- **Format Validation**: Validated SDF and PDB file formats

**Results:**
- Total files tracked: 6
- Important files preserved: 4
- Files by category: input (1), output (5), intermediate (1)
- All validations passed

## Command-Line Tools

### Cleanup Tool (`scripts/cleanup.py`)

```bash
# List all runs
python cleanup.py --project /path/to/project --list

# Dry run cleanup
python cleanup.py --project /path/to/project --run-id run_20260624_120000 --dry-run

# Cleanup with standard strategy
python cleanup.py --project /path/to/project --run-id run_20260624_120000 --strategy standard

# Cleanup and archive
python cleanup.py --project /path/to/project --run-id run_20260624_120000 --archive

# Interactive cleanup
python cleanup.py --project /path/to/project --run-id run_20260624_120000 --interactive
```

### Disk Usage Tool (`scripts/disk_usage.py`)

```bash
# Analyze entire project
python disk_usage.py --project /path/to/project

# Analyze specific run
python disk_usage.py --project /path/to/project --run-id run_20260624_120000

# Show largest files
python disk_usage.py --project /path/to/project --run-id run_20260624_120000 --show-files

# Get cleanup suggestions
python disk_usage.py --project /path/to/project --run-id run_20260624_120000 --suggest
```

## Key Design Decisions

### 1. Immutable Inputs
- Input files are never modified
- Tasks work on copies or symlinks in their work directory
- Ensures reproducibility and prevents data corruption
- Original inputs can be used for multiple runs

### 2. Run-Based Isolation
- Each pipeline execution gets its own timestamped directory
- Enables parallel runs without conflicts
- Historical runs preserved for comparison
- Clear separation between runs

### 3. Task Isolation
- Each task has its own directory with input/output/logs subdirectories
- Tasks cannot accidentally interfere with each other
- Simplifies debugging (all task artifacts in one place)
- Enables easy parallelization

### 4. Three-Tier Storage
- **inputs/**: permanent, never cleaned
- **work/**: temporary, cleaned based on policy
- **results/**: curated outputs, kept long-term

### 5. Smart Staging Strategy
- Small files (<100MB): copy to task directory
- Large files (>100MB): symlink to avoid duplication
- Read-only inputs: always symlink
- Modified inputs: always copy

### 6. File Tracking for Cleanup Intelligence
- Track every file with metadata (source task, category, importance)
- Cleanup decisions based on file relationships and usage
- Preserve files needed for reproducibility
- Remove files that can be regenerated

## Integration Points

The storage system is ready to integrate with:

- **Stage 2 (Configuration)**: Use PathManager to resolve input paths
- **Stage 3 (SLURM)**: Use PathManager for script and log paths
- **Stage 4 (Execution Engine)**: Use Workspace to prepare/finalize tasks
- **Stage 5 (Logging)**: Use PathManager for log file locations

## Usage Example

```python
from rapier.storage import Workspace, InputSpec, OutputSpec

# Initialize workspace
workspace = Workspace()
workspace.initialize(root='/path/to/project', run_id='run_001')

# Prepare task
task = Task(task_id='dock_001', method=docking_method, inputs={'ligands': 'ligands.sdf'})
workspace.prepare_task_workspace(task, input_spec=InputSpec(['ligands']))

# ... execute task ...

# Collect outputs
outputs = workspace.finalize_task_workspace(task, output_spec=OutputSpec(['results.sdf']))

# Promote important results
workspace.promote_to_results([outputs['results.sdf']])

# Analyze disk usage
usage = workspace.get_disk_usage()
print(f"Total: {usage['total_mb']:.2f} MB")

# Cleanup
workspace.cleanup(strategy='standard')

# Save state
workspace.save_state()
```

## File Size Thresholds

- **Large file threshold**: 100 MB (symlink vs copy decision)
- **Warning threshold**: Files >1 GB logged as warnings
- **Validation limits**: Configurable per file type

## Dependencies

### Required
- Python standard library (pathlib, os, shutil, tarfile, hashlib)

### Optional
- None (all functionality uses stdlib)

## Next Steps

### Integration Tasks

1. **Update Task class** (`rapier/core/task.py`):
   - Add properties: work_dir, input_dir, output_dir, log_dir
   - Initialize from PathManager

2. **Update TaskRunner** (`rapier/engine/task_runner.py`):
   - Use FileOrganizer for staging inputs
   - Use FileOrganizer for collecting outputs
   - Use FileTracker to register all created files

3. **Update ConfigParser** (`rapier/config/parser.py`):
   - Use PathManager to resolve input file paths from configuration
   - Support environment variable substitution

4. **Update SLURM components** (`rapier/slurm/`):
   - Use PathManager for script and log paths
   - Track generated scripts with FileTracker

### Optional Enhancements (Post-Stage 8)

- Automatic cleanup based on age (e.g., delete runs older than 90 days)
- Compression of intermediate files during execution
- S3/cloud storage integration for results
- Distributed file tracking across multiple nodes
- File deduplication based on checksums

## Conclusion

Stage 6 is fully implemented and tested. The file management system provides:

- ✅ Standard project structure with clear separation of concerns
- ✅ Smart file staging with symlinks for large files
- ✅ Comprehensive file tracking and metadata
- ✅ Flexible cleanup policies with dry-run support
- ✅ Format validation for common molecular file types
- ✅ Command-line tools for maintenance
- ✅ Archive support for long-term storage

All verification tests pass, and the system is ready for integration with the execution engine (Stage 4) and other components.
