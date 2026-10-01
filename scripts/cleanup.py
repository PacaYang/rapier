#!/usr/bin/env python3
"""Command-line tool for cleaning up pipeline runs."""

import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rapier.storage import Workspace, POLICIES


def format_size(size_bytes: int) -> str:
    """Format size in bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def list_runs(project_root: str) -> None:
    """List all runs in the project."""
    from rapier.storage import ProjectStructure

    structure = ProjectStructure(project_root)
    runs = structure.list_runs()

    if not runs:
        print("No runs found.")
        return

    print(f"\nFound {len(runs)} run(s):\n")
    for run_dir in runs:
        run_id = run_dir.name
        info = structure.get_run_info(run_id)

        print(f"  {run_id}")
        print(f"    Tasks: {info['task_count']}")
        print(f"    Size: {format_size(info['size_bytes'])}")
        print(f"    Created: {info['created']}")
        print()


def cleanup_run(
    project_root: str,
    run_id: str,
    strategy: str,
    dry_run: bool,
    archive: bool,
    keep_logs: bool
) -> None:
    """Clean up a specific run."""
    workspace = Workspace()

    try:
        workspace.initialize(root=project_root, run_id=run_id)
    except Exception as e:
        print(f"Error initializing workspace: {e}")
        sys.exit(1)

    # Show disk usage before cleanup
    print(f"\n{'='*60}")
    print(f"Cleanup Run: {run_id}")
    print(f"Strategy: {strategy}")
    print(f"{'='*60}\n")

    usage_before = workspace.get_disk_usage()
    print(f"Disk usage before cleanup:")
    print(f"  Total: {format_size(usage_before['total_bytes'])}")
    for category, stats in usage_before['by_category'].items():
        print(f"    {category}: {stats['count']} files, {format_size(stats['size_bytes'])}")

    # Modify strategy if keep_logs is set
    if keep_logs and strategy in ['standard', 'aggressive']:
        print(f"\n  Note: Keeping logs as requested")

    # Perform cleanup
    if dry_run:
        print(f"\n{'='*60}")
        print("DRY RUN - No files will be deleted")
        print(f"{'='*60}\n")

    result = workspace.cleanup(strategy=strategy, dry_run=dry_run)

    print(f"\nCleanup results:")
    print(f"  Files deleted: {result['deleted_files']}")
    print(f"  Space freed: {format_size(result['freed_bytes'])}")

    if dry_run:
        print(f"\n  (Dry run - no files were actually deleted)")
    else:
        # Show disk usage after cleanup
        usage_after = workspace.get_disk_usage()
        print(f"\nDisk usage after cleanup:")
        print(f"  Total: {format_size(usage_after['total_bytes'])}")

    # Archive if requested
    if archive and not dry_run:
        print(f"\nArchiving run...")
        try:
            archive_path = workspace.archive_run(compression='gz', cleanup_after=True)
            print(f"  Archived to: {archive_path}")
        except Exception as e:
            print(f"  Error archiving: {e}")


def interactive_cleanup(project_root: str, run_id: str) -> None:
    """Interactive cleanup with file preview."""
    workspace = Workspace()
    workspace.initialize(root=project_root, run_id=run_id)

    print(f"\n{'='*60}")
    print(f"Interactive Cleanup: {run_id}")
    print(f"{'='*60}\n")

    # Show available strategies
    print("Available strategies:")
    for name, policy in POLICIES.items():
        print(f"  {name}:")
        print(f"    Keep inputs: {policy.keep_inputs}")
        print(f"    Keep outputs: {policy.keep_outputs}")
        print(f"    Keep logs: {policy.keep_logs}")
        print(f"    Keep intermediates: {policy.keep_intermediates}")
        print()

    # Get user choice
    strategy = input("Choose strategy (minimal/standard/aggressive): ").strip().lower()
    if strategy not in POLICIES:
        print(f"Invalid strategy: {strategy}")
        return

    # Preview files
    candidates = workspace.cleanup_manager.get_cleanup_candidates(strategy)
    print(f"\nFiles to be deleted ({len(candidates)}):")

    if len(candidates) > 20:
        print(f"  (showing first 20 of {len(candidates)})")
        for path in candidates[:20]:
            print(f"    {path}")
    else:
        for path in candidates:
            print(f"    {path}")

    # Confirm
    confirm = input(f"\nProceed with cleanup? (yes/no): ").strip().lower()
    if confirm in ['yes', 'y']:
        result = workspace.cleanup(strategy=strategy, dry_run=False)
        print(f"\nDeleted {result['deleted_files']} files")
        print(f"Freed {format_size(result['freed_bytes'])}")
    else:
        print("Cleanup cancelled")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Clean up Rapier pipeline runs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all runs
  python cleanup.py --project /path/to/project --list

  # Dry run cleanup with standard strategy
  python cleanup.py --project /path/to/project --run-id run_20260624_120000 --dry-run

  # Clean up with standard strategy
  python cleanup.py --project /path/to/project --run-id run_20260624_120000 --strategy standard

  # Clean up and archive
  python cleanup.py --project /path/to/project --run-id run_20260624_120000 --archive

  # Interactive cleanup
  python cleanup.py --project /path/to/project --run-id run_20260624_120000 --interactive
"""
    )

    parser.add_argument(
        '--project',
        required=True,
        help='Project root directory'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all runs'
    )
    parser.add_argument(
        '--run-id',
        help='Run identifier to clean up'
    )
    parser.add_argument(
        '--strategy',
        choices=['minimal', 'standard', 'aggressive'],
        default='standard',
        help='Cleanup strategy (default: standard)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    parser.add_argument(
        '--archive',
        action='store_true',
        help='Archive run after cleanup'
    )
    parser.add_argument(
        '--keep-logs',
        action='store_true',
        help='Keep log files even with aggressive strategy'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Interactive mode with file preview'
    )

    args = parser.parse_args()

    # List runs
    if args.list:
        list_runs(args.project)
        return

    # Require run-id for cleanup
    if not args.run_id:
        print("Error: --run-id required for cleanup operations")
        parser.print_help()
        sys.exit(1)

    # Interactive or automatic cleanup
    if args.interactive:
        interactive_cleanup(args.project, args.run_id)
    else:
        cleanup_run(
            args.project,
            args.run_id,
            args.strategy,
            args.dry_run,
            args.archive,
            args.keep_logs
        )


if __name__ == '__main__':
    main()
