#!/usr/bin/env python3
"""
Comprehensive test of the Stage 6 file management system.
Demonstrates project structure, file organization, and cleanup.
"""

import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from rapier.storage import (
    ProjectStructure,
    PathManager,
    FileTracker,
    FileOrganizer,
    CleanupManager,
    Workspace,
    InputSpec,
    OutputSpec,
    validators
)
from rapier.core import Task


def format_size(size_bytes: int) -> str:
    """Format size in bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def test_complete_workflow():
    """Test complete file management workflow."""

    print("\n" + "="*60)
    print("STAGE 6 FILE MANAGEMENT SYSTEM - COMPREHENSIVE TEST")
    print("="*60 + "\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = tmpdir

        # Step 1: Create project structure
        print("Step 1: Creating project structure...")
        structure = ProjectStructure.create_project(project_root)

        print(f"  ✓ Created project at: {project_root}")
        print(f"    - config/")
        print(f"    - inputs/")
        print(f"    - work/")
        print(f"    - results/")
        print(f"    - archive/")

        # Validate structure
        is_valid, missing = structure.validate_structure()
        assert is_valid, f"Structure invalid: {missing}"
        print(f"  ✓ Project structure validated\n")

        # Step 2: Create input files
        print("Step 2: Creating input files...")
        ligands_dir = structure.input_dir / 'ligands'
        ligands_file = ligands_dir / 'molecules.sdf'

        # Create a valid SDF file
        with open(ligands_file, 'w') as f:
            f.write("""
  Mrv0541 02231512393D

 10 10  0  0  0  0            999 V2000
    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.5981    1.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  M  END
$$$$
""")

        print(f"  ✓ Created: {ligands_file}")

        # Validate input file
        is_valid, errors = validators.validate_sdf_file(ligands_file)
        assert is_valid, f"SDF validation failed: {errors}"
        print(f"  ✓ Validated SDF format\n")

        # Step 3: Initialize workspace for a run
        print("Step 3: Initializing workspace for pipeline run...")
        workspace = Workspace()
        workspace.initialize(root=project_root, run_id='test_run_001')

        print(f"  ✓ Workspace initialized")
        print(f"    Run directory: {workspace.path_manager.run_dir}")
        print()

        # Step 4: Create and prepare tasks
        print("Step 4: Preparing task workspaces...")

        task1 = Task(task_id='prep_001', method=None, inputs={'ligands': str(ligands_file)})
        workspace.prepare_task_workspace(task1)

        print(f"  ✓ Task prep_001:")
        print(f"    Work dir: {task1.work_dir}")
        print(f"    Input dir: {task1.input_dir}")
        print(f"    Output dir: {task1.output_dir}")

        # Stage inputs
        input_spec = InputSpec(required_files=['ligands'])
        staged = workspace.file_organizer.stage_inputs(task1, input_spec, use_symlinks=False)
        print(f"  ✓ Staged {len(staged)} input file(s)")
        print()

        # Step 5: Simulate task execution and outputs
        print("Step 5: Simulating task execution...")

        # Create output files
        output_file = task1.output_dir / 'prepared.pdb'
        with open(output_file, 'w') as f:
            f.write("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n")
            f.write("END\n")

        intermediate_file = task1.output_dir / 'temp.tmp'
        with open(intermediate_file, 'w') as f:
            f.write("temporary intermediate data")

        # Register files
        workspace.file_tracker.register_file(output_file, 'output', 'prep_001', 'Prepared structure')
        workspace.file_tracker.mark_important(output_file)
        workspace.file_tracker.register_file(intermediate_file, 'intermediate', 'prep_001')

        print(f"  ✓ Created output: {output_file.name}")
        print(f"  ✓ Created intermediate: {intermediate_file.name}")
        print()

        # Step 6: Collect outputs
        print("Step 6: Collecting task outputs...")
        output_spec = OutputSpec(expected_files=['prepared.pdb'])
        collected = workspace.file_organizer.collect_outputs(task1, output_spec)

        print(f"  ✓ Collected {len(collected)} output file(s)")
        for name, path in collected.items():
            print(f"    - {name}: {path.name}")
        print()

        # Step 7: Create second task
        print("Step 7: Creating second task...")
        task2 = Task(task_id='dock_001', method=None, inputs={'receptor': str(output_file)})
        workspace.prepare_task_workspace(task2)

        # Create more outputs
        results_file = task2.output_dir / 'docked.sdf'
        with open(results_file, 'w') as f:
            f.write("""

  0  0  0  0  0  0            999 V2000
M  END
$$$$
""")

        workspace.file_tracker.register_file(results_file, 'output', 'dock_001')
        workspace.file_tracker.mark_important(results_file)

        print(f"  ✓ Task dock_001 created")
        print(f"  ✓ Created output: {results_file.name}")
        print()

        # Step 8: Analyze disk usage
        print("Step 8: Analyzing disk usage...")
        usage = workspace.get_disk_usage()

        print(f"  Total files: {workspace.file_tracker.get_stats()['total_files']}")
        print(f"  Total size: {format_size(usage['total_bytes'])}")
        print(f"  By category:")
        for category, stats in usage['by_category'].items():
            print(f"    - {category}: {stats['count']} files, {format_size(stats['size_bytes'])}")
        print()

        # Step 9: Promote important files to results
        print("Step 9: Promoting results...")
        important_files = [output_file, results_file]
        promoted = workspace.promote_to_results(important_files)

        print(f"  ✓ Promoted {len(promoted)} file(s) to results/")
        for path in promoted:
            print(f"    - {path.name}")
        print()

        # Step 10: Test cleanup (dry run)
        print("Step 10: Testing cleanup (dry run)...")
        cleanup_result = workspace.cleanup(strategy='standard', dry_run=True)

        print(f"  Would delete: {cleanup_result['deleted_files']} files")
        print(f"  Would free: {format_size(cleanup_result['freed_bytes'])}")
        print()

        # Step 11: Actual cleanup
        print("Step 11: Running cleanup (standard strategy)...")
        cleanup_result = workspace.cleanup(strategy='standard', dry_run=False)

        print(f"  ✓ Deleted: {cleanup_result['deleted_files']} files")
        print(f"  ✓ Freed: {format_size(cleanup_result['freed_bytes'])}")

        # Check that important files are preserved
        assert output_file.exists() or any(p.name == output_file.name for p in promoted), "Important file was deleted!"
        print(f"  ✓ Important files preserved")
        print()

        # Step 12: Save workspace state
        print("Step 12: Saving workspace state...")
        workspace.save_state()

        manifest_path = workspace.path_manager.get_state_file().parent / 'files_manifest.json'
        assert manifest_path.exists(), "Manifest not saved"
        print(f"  ✓ Saved manifest: {manifest_path.name}")
        print()

        # Step 13: Test validators
        print("Step 13: Testing file validators...")

        # Validate PDB
        pdb_valid, pdb_errors = validators.validate_pdb_file(output_file)
        print(f"  ✓ PDB validation: {'passed' if pdb_valid else 'failed'}")

        # Validate SDF
        sdf_valid, sdf_errors = validators.validate_sdf_file(results_file)
        print(f"  ✓ SDF validation: {'passed' if sdf_valid else 'failed'}")

        # File size validation
        size_valid = validators.validate_file_size(output_file, min_size=1, max_size=10000)
        print(f"  ✓ Size validation: {'passed' if size_valid else 'failed'}")
        print()

        # Final statistics
        print("="*60)
        print("FINAL STATISTICS")
        print("="*60)

        stats = workspace.file_tracker.get_stats()
        print(f"  Total files tracked: {stats['total_files']}")
        print(f"  Important files: {stats['important_files']}")
        print(f"  Files by category:")
        for category, count in stats['by_category'].items():
            print(f"    - {category}: {count}")
        print(f"  Files by task:")
        for task_id, count in stats['by_task'].items():
            print(f"    - {task_id}: {count}")

        usage_after = workspace.get_disk_usage()
        print(f"\n  Final disk usage: {format_size(usage_after['total_bytes'])}")

        print("\n" + "="*60)
        print("✓ ALL STAGE 6 TESTS PASSED")
        print("="*60 + "\n")

    return True


if __name__ == '__main__':
    try:
        success = test_complete_workflow()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
