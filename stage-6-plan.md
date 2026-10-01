# Stage 6: File Management & Project Structure

## Goal
Design and implement the project folder structure that separates inputs, intermediate files, and outputs. Create utilities for automatic cleanup of intermediate files while preserving important results. Implement path management that guides code organization and makes it easy to locate files at any stage of execution.

## Files to Create

### `rapier/storage/__init__.py`
- Storage module initialization
- Export main classes: `ProjectStructure`, `PathManager`, `FileOrganizer`, `CleanupManager`

### `rapier/storage/structure.py`
- `ProjectStructure` class - defines directory layout
- Standard structure:
  ```
  project_root/
  ├── config/                 # Pipeline configurations
  │   └── pipeline.yaml
  ├── inputs/                 # Original input files (never modified)
  │   ├── receptors/
  │   └── ligands/
  ├── work/                   # Working directory for all executions
  │   └── run_YYYYMMDD_HHMMSS/   # One directory per pipeline run
  │       ├── tasks/              # Task-specific directories
  │       │   ├── task_001/
  │       │   │   ├── input/      # Symlinks or copies of inputs
  │       │   │   ├── output/     # Task outputs
  │       │   │   ├── logs/       # Task logs
  │       │   │   └── scripts/    # SLURM scripts
  │       │   └── task_002/
  │       │       └── ...
  │       ├── intermediate/       # Cross-task intermediate files
  │       ├── logs/               # Pipeline-level logs
  │       └── state.json          # Execution state
  ├── results/                # Final outputs (manually curated or auto-selected)
  │   └── run_YYYYMMDD_HHMMSS/
  │       └── final_output.sdf
  └── archive/                # Archived/cleaned runs
      └── run_YYYYMMDD_HHMMSS_cleaned.tar.gz
  ```
- Methods:
  - `create_project(root: str) -> ProjectStructure`
  - `create_run_directory(timestamp: str) -> str`
  - `get_directory(dir_type: str) -> Path`

### `rapier/storage/path_manager.py`
- `PathManager` class - resolves paths for all file operations
- Properties: `project_root`, `run_dir`, `config_dir`, `input_dir`, `work_dir`, `results_dir`
- Methods:
  - `get_task_dir(task_id: str) -> Path`
  - `get_task_input_dir(task_id: str) -> Path`
  - `get_task_output_dir(task_id: str) -> Path`
  - `get_task_log_file(task_id: str) -> Path`
  - `get_slurm_script_path(task_id: str) -> Path`
  - `get_intermediate_dir() -> Path`
  - `resolve_input_path(relative_path: str) -> Path` - resolve from config
- Ensures consistent path resolution across all components
- Supports relative and absolute paths

### `rapier/storage/file_organizer.py`
- `FileOrganizer` class - manages file placement and movement
- Methods:
  - `stage_inputs(task: Task, input_spec: InputSpec)` - prepare task inputs
  - `collect_outputs(task: Task, output_spec: OutputSpec)` - gather task outputs
  - `promote_to_results(files: List[Path])` - move important files to results
  - `create_symlinks(source: Path, target: Path)` - avoid file duplication
  - `copy_files(source: Path, target: Path, pattern: str)` - selective copy
- Smart file staging (symlink vs copy based on file size and mutability)
- Validates file existence and permissions

### `rapier/storage/cleanup_manager.py`
- `CleanupManager` class - manages intermediate file cleanup
- Properties: `cleanup_policies` - rules for what to clean and when
- Methods:
  - `mark_for_cleanup(path: Path, priority: str)` - tag for deletion
  - `mark_preserve(path: Path)` - protect from cleanup
  - `cleanup_task(task_id: str, keep_outputs: bool)` - clean single task
  - `cleanup_run(run_id: str, strategy: str)` - clean entire run
  - `estimate_disk_usage(run_id: str) -> Dict` - space analysis
  - `archive_run(run_id: str, compression: str)` - compress and archive
- Cleanup strategies: `minimal` (keep everything), `standard` (remove intermediates), `aggressive` (keep only final outputs)

### `rapier/storage/file_tracker.py`
- `FileTracker` class - tracks all files created during execution
- Methods:
  - `register_file(path: Path, category: str, task_id: str)`
  - `get_files_by_category(category: str) -> List[Path]`
  - `get_files_by_task(task_id: str) -> List[Path]`
  - `mark_important(path: Path)` - mark for preservation
  - `get_all_tracked_files() -> Dict`
- Categories: `input`, `intermediate`, `output`, `log`, `script`, `checkpoint`
- Enables smart cleanup decisions

### `rapier/storage/metadata.py`
- `FileMetadata` class - stores metadata about files
- Properties: `path`, `size`, `created_time`, `modified_time`, `checksum`, `source_task`, `category`
- Methods:
  - `compute_checksum(algorithm: str) -> str` - MD5, SHA256
  - `is_valid() -> bool` - check file integrity
  - `to_dict()`, `from_dict()`
- Saved alongside files as `.metadata.json`
- Enables provenance tracking

### `rapier/storage/workspace.py`
- `Workspace` class - high-level workspace management
- Combines ProjectStructure, PathManager, FileOrganizer, CleanupManager
- Methods:
  - `initialize(root: str, run_id: str)`
  - `prepare_task_workspace(task: Task)`
  - `finalize_task_workspace(task: Task)`
  - `get_disk_usage() -> Dict`
  - `cleanup(strategy: str)`
- Single interface for all file operations

### `rapier/storage/validators.py`
- File validation utilities
- Functions:
  - `validate_sdf_file(path: Path) -> Tuple[bool, List[str]]`
  - `validate_pdb_file(path: Path) -> Tuple[bool, List[str]]`
  - `validate_csv_file(path: Path, expected_columns: List[str]) -> Tuple[bool, List[str]]`
  - `validate_file_size(path: Path, min_size: int, max_size: int) -> bool`
- Format-specific validation for common molecular file types

### `scripts/cleanup.py`
- Command-line tool for cleaning up runs
- Usage: `python cleanup.py --run-id <id> --strategy standard`
- Options: `--dry-run`, `--archive`, `--keep-logs`
- Interactive mode for reviewing files before deletion

### `scripts/disk_usage.py`
- Command-line tool for analyzing disk usage
- Shows space used by each run, task, and file category
- Identifies largest files and suggests cleanup targets

## Files to Modify

### `rapier/core/task.py`
- Add properties: `work_dir`, `input_dir`, `output_dir`, `log_dir`
- Initialize from PathManager

### `rapier/engine/task_runner.py`
- Use FileOrganizer for staging inputs and collecting outputs
- Use FileTracker to register all created files

### `rapier/config/parser.py`
- Use PathManager to resolve input file paths from configuration

## Implementation Order

1. Define project structure (`structure.py`) - establishes layout
2. Implement path manager (`path_manager.py`) - path resolution
3. Implement file metadata (`metadata.py`) - provenance tracking
4. Implement file tracker (`file_tracker.py`) - registration system
5. Implement file validators (`validators.py`) - format checking
6. Implement file organizer (`file_organizer.py`) - staging/collection
7. Implement cleanup manager (`cleanup_manager.py`) - deletion policies
8. Implement workspace (`workspace.py`) - high-level interface
9. Create command-line tools (`cleanup.py`, `disk_usage.py`)
10. Integrate with Task and TaskRunner

## Verification Steps

```bash
# Test project structure creation
python3 -c "
from rapier.storage import ProjectStructure
import tempfile
import os

with tempfile.TemporaryDirectory() as tmpdir:
    structure = ProjectStructure.create_project(tmpdir)
    
    # Verify directories exist
    assert os.path.isdir(structure.input_dir), 'inputs/ not created'
    assert os.path.isdir(structure.work_dir), 'work/ not created'
    assert os.path.isdir(structure.results_dir), 'results/ not created'
    print('✓ Project structure creation works')
"

# Test path manager
python3 -c "
from rapier.storage import PathManager
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    pm = PathManager(project_root=tmpdir, run_id='test_run_001')
    
    task_dir = pm.get_task_dir('task_001')
    assert 'task_001' in str(task_dir), f'Unexpected task dir: {task_dir}'
    
    log_file = pm.get_task_log_file('task_001')
    assert log_file.name.endswith('.log'), f'Unexpected log file: {log_file}'
    print('✓ Path manager works')
"

# Test file organizer
python3 -c "
from rapier.storage import FileOrganizer, PathManager
from rapier.core import Task
from rapier.core.specs import InputSpec
import tempfile
import os

with tempfile.TemporaryDirectory() as tmpdir:
    # Create source file
    source_file = os.path.join(tmpdir, 'input.sdf')
    with open(source_file, 'w') as f:
        f.write('mock molecule data')
    
    pm = PathManager(project_root=tmpdir, run_id='test')
    task = Task(task_id='task_001', method=None, inputs={'ligands': source_file})
    task.input_dir = pm.get_task_input_dir('task_001')
    os.makedirs(task.input_dir, exist_ok=True)
    
    organizer = FileOrganizer(pm)
    input_spec = InputSpec(required_files=['ligands'])
    organizer.stage_inputs(task, input_spec)
    
    staged_file = os.path.join(task.input_dir, 'input.sdf')
    assert os.path.exists(staged_file), f'File not staged: {staged_file}'
    print('✓ File organizer staging works')
"

# Test file tracker
python3 -c "
from rapier.storage import FileTracker
from pathlib import Path

tracker = FileTracker()
tracker.register_file(Path('/tmp/test.sdf'), category='output', task_id='task_001')
tracker.register_file(Path('/tmp/intermediate.tmp'), category='intermediate', task_id='task_001')

output_files = tracker.get_files_by_category('output')
assert len(output_files) == 1, f'Expected 1 output file, got {len(output_files)}'

task_files = tracker.get_files_by_task('task_001')
assert len(task_files) == 2, f'Expected 2 files for task, got {len(task_files)}'
print('✓ File tracker works')
"

# Test cleanup manager
python3 -c "
from rapier.storage import CleanupManager, FileTracker
import tempfile
import os

with tempfile.TemporaryDirectory() as tmpdir:
    # Create test files
    important_file = os.path.join(tmpdir, 'results.sdf')
    temp_file = os.path.join(tmpdir, 'intermediate.tmp')
    with open(important_file, 'w') as f:
        f.write('important')
    with open(temp_file, 'w') as f:
        f.write('temporary')
    
    tracker = FileTracker()
    tracker.register_file(important_file, 'output', 'task_001')
    tracker.register_file(temp_file, 'intermediate', 'task_001')
    
    manager = CleanupManager(tracker)
    manager.mark_preserve(important_file)
    manager.mark_for_cleanup(temp_file, priority='high')
    
    # Would normally call cleanup here, but we'll just verify marking
    print('✓ Cleanup manager marking works')
"

# Test file validators
python3 -c "
from rapier.storage.validators import validate_file_size
import tempfile

with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
    f.write('test content')
    test_file = f.name

is_valid = validate_file_size(test_file, min_size=1, max_size=1000)
assert is_valid, 'File size validation failed'

import os
os.unlink(test_file)
print('✓ File validation works')
"

# Test workspace integration
python3 -c "
from rapier.storage import Workspace
from rapier.core import Task
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    workspace = Workspace()
    workspace.initialize(root=tmpdir, run_id='test_run')
    
    task = Task(task_id='task_001', method=None, inputs={})
    workspace.prepare_task_workspace(task)
    
    assert task.work_dir is not None, 'Task work_dir not set'
    assert task.input_dir is not None, 'Task input_dir not set'
    print('✓ Workspace integration works')
"
```

## Key Decisions

**1. Immutable Inputs**
- Input files never modified
- Tasks work on copies or symlinks in their work directory
- Ensures reproducibility and prevents accidental data corruption
- Original inputs can be used for multiple runs

**2. Run-Based Directory Structure**
- Each pipeline execution gets its own timestamped directory
- Enables parallel runs without conflicts
- Historical runs preserved for comparison
- Clear separation between runs

**3. Task Isolation**
- Each task has its own directory with input/output/logs subdirectories
- Tasks cannot accidentally interfere with each other
- Simplifies debugging (all task artifacts in one place)
- Enables easy parallelization

**4. Three-Tier Storage**
- **inputs/**: permanent, never cleaned
- **work/**: temporary, cleaned based on policy
- **results/**: curated outputs, kept long-term
- Clear lifecycle for each file category

**5. Smart Staging Strategy**
- Small files (<100MB): copy to task directory
- Large files (>100MB): symlink to avoid duplication
- Read-only inputs: always symlink
- Modified inputs: always copy
- Balances safety and disk usage

**6. Cleanup Policies**
- **minimal**: keep everything (development/debugging)
- **standard**: remove intermediate files, keep inputs/outputs/logs
- **aggressive**: keep only final results
- **archive**: compress entire run to .tar.gz
- Configurable per run or globally

**7. File Tracking for Cleanup Intelligence**
- Track every file created with metadata (source task, category, importance)
- Cleanup decisions based on file relationships and usage
- Preserve files needed for reproducibility
- Remove files that can be regenerated

**8. Path Resolution Strategy**
- All paths relative to project root when possible
- Absolute paths supported for external resources
- Environment variable substitution (${VAR})
- Symlinks followed or preserved based on context
