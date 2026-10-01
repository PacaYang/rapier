#!/usr/bin/env python3
"""Command-line tool for analyzing disk usage of pipeline runs."""

import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rapier.storage import Workspace, ProjectStructure


def format_size(size_bytes: int) -> str:
    """Format size in bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def analyze_project(project_root: str) -> None:
    """Analyze disk usage across entire project."""
    structure = ProjectStructure(project_root)
    runs = structure.list_runs()

    print(f"\n{'='*60}")
    print(f"Project Disk Usage Analysis")
    print(f"Project: {project_root}")
    print(f"{'='*60}\n")

    if not runs:
        print("No runs found.")
        return

    total_size = 0
    run_stats = []

    for run_dir in runs:
        run_id = run_dir.name
        info = structure.get_run_info(run_id)
        total_size += info['size_bytes']
        run_stats.append({
            'run_id': run_id,
            'size': info['size_bytes'],
            'tasks': info['task_count'],
            'created': info['created']
        })

    # Sort by size (largest first)
    run_stats.sort(key=lambda x: x['size'], reverse=True)

    print(f"Total runs: {len(runs)}")
    print(f"Total disk usage: {format_size(total_size)}\n")

    print(f"{'Run ID':<30} {'Size':<15} {'Tasks':<10} {'Created'}")
    print('-' * 80)

    for stat in run_stats:
        print(f"{stat['run_id']:<30} {format_size(stat['size']):<15} {stat['tasks']:<10} {stat['created'][:10]}")


def analyze_run(project_root: str, run_id: str, show_files: bool = False) -> None:
    """Analyze disk usage for a specific run."""
    workspace = Workspace()

    try:
        workspace.initialize(root=project_root, run_id=run_id)
    except Exception as e:
        print(f"Error initializing workspace: {e}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Run Disk Usage Analysis: {run_id}")
    print(f"{'='*60}\n")

    usage = workspace.get_disk_usage()

    print(f"Total: {format_size(usage['total_bytes'])}")
    print(f"  ({usage['total_mb']:.2f} MB, {usage['total_gb']:.2f} GB)\n")

    print("By category:")
    print(f"{'Category':<15} {'Files':<10} {'Size':<15} {'% of Total'}")
    print('-' * 60)

    for category, stats in sorted(usage['by_category'].items()):
        percentage = (stats['size_bytes'] / usage['total_bytes'] * 100) if usage['total_bytes'] > 0 else 0
        print(f"{category:<15} {stats['count']:<10} {format_size(stats['size_bytes']):<15} {percentage:.1f}%")

    # Show largest files if requested
    if show_files:
        print(f"\nLargest files:")
        all_files = workspace.get_file_tracker().get_all_tracked_files()

        # Get file sizes
        file_list = []
        for path_str, metadata in all_files.items():
            path = Path(path_str)
            if path.exists() and metadata.size:
                file_list.append({
                    'path': path_str,
                    'size': metadata.size,
                    'category': metadata.category
                })

        # Sort by size
        file_list.sort(key=lambda x: x['size'], reverse=True)

        # Show top 20
        print(f"{'Size':<15} {'Category':<12} {'Path'}")
        print('-' * 80)
        for file_info in file_list[:20]:
            rel_path = file_info['path']
            if len(rel_path) > 50:
                rel_path = '...' + rel_path[-47:]
            print(f"{format_size(file_info['size']):<15} {file_info['category']:<12} {rel_path}")


def suggest_cleanup(project_root: str, run_id: str) -> None:
    """Suggest cleanup targets to free up space."""
    workspace = Workspace()
    workspace.initialize(root=project_root, run_id=run_id)

    print(f"\n{'='*60}")
    print(f"Cleanup Suggestions: {run_id}")
    print(f"{'='*60}\n")

    usage = workspace.get_disk_usage()

    # Calculate savings for each strategy
    from rapier.storage import POLICIES

    print("Potential space savings:\n")

    for strategy_name, policy in POLICIES.items():
        # Calculate what would be freed
        saved_bytes = 0
        for category, stats in usage['by_category'].items():
            if not policy.should_keep(category):
                saved_bytes += stats['size_bytes']

        percentage = (saved_bytes / usage['total_bytes'] * 100) if usage['total_bytes'] > 0 else 0

        print(f"  {strategy_name}:")
        print(f"    Would free: {format_size(saved_bytes)} ({percentage:.1f}%)")
        print(f"    Keeps: ", end='')
        keeps = []
        if policy.keep_inputs:
            keeps.append("inputs")
        if policy.keep_outputs:
            keeps.append("outputs")
        if policy.keep_logs:
            keeps.append("logs")
        if policy.keep_intermediates:
            keeps.append("intermediates")
        print(', '.join(keeps) if keeps else "only final results")
        print()

    # Show specific recommendations
    print("Recommendations:")

    # Check for large intermediate files
    intermediates = usage['by_category'].get('intermediate', {})
    if intermediates and intermediates['size_bytes'] > 100 * 1024 * 1024:  # > 100 MB
        print(f"  • {intermediates['count']} intermediate files use {format_size(intermediates['size_bytes'])}")
        print(f"    → Run: python cleanup.py --project {project_root} --run-id {run_id} --strategy standard")

    # Check for old runs
    structure = ProjectStructure(project_root)
    run_info = structure.get_run_info(run_id)
    from datetime import datetime, timedelta
    created = datetime.fromisoformat(run_info['created'])
    age_days = (datetime.now() - created).days

    if age_days > 30:
        print(f"  • This run is {age_days} days old")
        print(f"    → Consider archiving: python cleanup.py --project {project_root} --run-id {run_id} --archive")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Analyze disk usage for Rapier pipeline runs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze entire project
  python disk_usage.py --project /path/to/project

  # Analyze specific run
  python disk_usage.py --project /path/to/project --run-id run_20260624_120000

  # Show largest files
  python disk_usage.py --project /path/to/project --run-id run_20260624_120000 --show-files

  # Get cleanup suggestions
  python disk_usage.py --project /path/to/project --run-id run_20260624_120000 --suggest
"""
    )

    parser.add_argument(
        '--project',
        required=True,
        help='Project root directory'
    )
    parser.add_argument(
        '--run-id',
        help='Run identifier to analyze'
    )
    parser.add_argument(
        '--show-files',
        action='store_true',
        help='Show largest files'
    )
    parser.add_argument(
        '--suggest',
        action='store_true',
        help='Suggest cleanup targets'
    )

    args = parser.parse_args()

    if args.run_id:
        # Analyze specific run
        if args.suggest:
            suggest_cleanup(args.project, args.run_id)
        else:
            analyze_run(args.project, args.run_id, args.show_files)
    else:
        # Analyze entire project
        analyze_project(args.project)


if __name__ == '__main__':
    main()
