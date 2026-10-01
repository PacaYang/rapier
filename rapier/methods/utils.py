"""Utility functions for method implementations.

This module provides common utilities for parsing specifications, checking
executables, and working with molecular data files.
"""

from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import shutil
import re
import subprocess


def parse_box_spec(spec: str) -> Dict[str, List[float]]:
    """Parse a box specification string for docking.

    Expected format: "center=x,y,z size=dx,dy,dz"
    Example: "center=1.5,2.0,3.0 size=20,20,25"

    Args:
        spec: Box specification string

    Returns:
        Dict with 'center' and 'size' keys, each containing [x, y, z] floats

    Raises:
        ValueError: If the specification format is invalid
    """
    result = {}

    # Extract center
    center_match = re.search(r'center=([+-]?\d+\.?\d*),([+-]?\d+\.?\d*),([+-]?\d+\.?\d*)', spec)
    if center_match:
        result['center'] = [float(center_match.group(1)),
                           float(center_match.group(2)),
                           float(center_match.group(3))]
    else:
        raise ValueError(f"Invalid box spec: missing or malformed 'center=x,y,z' in '{spec}'")

    # Extract size
    size_match = re.search(r'size=([+-]?\d+\.?\d*),([+-]?\d+\.?\d*),([+-]?\d+\.?\d*)', spec)
    if size_match:
        result['size'] = [float(size_match.group(1)),
                         float(size_match.group(2)),
                         float(size_match.group(3))]
    else:
        raise ValueError(f"Invalid box spec: missing or malformed 'size=dx,dy,dz' in '{spec}'")

    return result


def check_executable(name: str) -> bool:
    """Check if an executable is available in PATH.

    Args:
        name: Name of the executable (e.g., 'vina', 'obabel', 'python3')

    Returns:
        True if the executable exists and is accessible, False otherwise
    """
    return shutil.which(name) is not None


def check_executables(names: List[str]) -> Tuple[bool, List[str]]:
    """Check multiple executables at once.

    Args:
        names: List of executable names to check

    Returns:
        Tuple of (all_found, missing_list)
        - all_found: True if all executables were found
        - missing_list: List of missing executable names
    """
    missing = [name for name in names if not check_executable(name)]
    return len(missing) == 0, missing


def load_molecules_from_sdf(path: Path) -> List[Dict[str, Any]]:
    """Load molecules from an SDF file.

    Args:
        path: Path to the SDF file

    Returns:
        List of molecule dictionaries with basic properties

    Note:
        This is a placeholder. Real implementation would use RDKit:
        from rdkit import Chem
        suppl = Chem.SDMolSupplier(str(path))
        return [mol for mol in suppl if mol is not None]
    """
    if not path.exists():
        raise FileNotFoundError(f"SDF file not found: {path}")

    # Placeholder implementation - count molecules in SDF
    molecules = []
    with open(path, 'r') as f:
        mol_data = []
        for line in f:
            mol_data.append(line)
            if line.strip() == '$$$$':
                molecules.append({'sdf_data': ''.join(mol_data)})
                mol_data = []

    return molecules


def write_molecules_to_sdf(molecules: List[Any], path: Path) -> None:
    """Write molecules to an SDF file.

    Args:
        molecules: List of molecule objects (RDKit Mol objects or dict with sdf_data)
        path: Output SDF file path

    Note:
        This is a placeholder. Real implementation would use RDKit:
        writer = Chem.SDWriter(str(path))
        for mol in molecules:
            writer.write(mol)
        writer.close()
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w') as f:
        for mol in molecules:
            if isinstance(mol, dict) and 'sdf_data' in mol:
                f.write(mol['sdf_data'])
            else:
                # Placeholder for RDKit Mol objects
                f.write(str(mol))
                f.write('\n$$$$\n')


def parse_vina_output(output: str) -> List[Dict[str, Any]]:
    """Parse Vina output to extract docking scores.

    Args:
        output: Vina stdout/stderr text

    Returns:
        List of pose dictionaries with mode, affinity, and RMSD values

    Example output to parse:
        mode |   affinity | dist from best mode
             | (kcal/mol) | rmsd l.b.| rmsd u.b.
        -----+------------+----------+----------
           1        -8.2      0.000      0.000
           2        -7.9      1.234      2.345
    """
    poses = []

    lines = output.split('\n')
    in_results = False

    for line in lines:
        # Start of results section
        if 'mode' in line and 'affinity' in line:
            in_results = True
            continue

        # Skip separator line
        if in_results and '-----' in line:
            continue

        # Parse result line
        if in_results and line.strip():
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                try:
                    poses.append({
                        'mode': int(parts[0]),
                        'affinity': float(parts[1]),
                        'rmsd_lb': float(parts[2]),
                        'rmsd_ub': float(parts[3])
                    })
                except (ValueError, IndexError):
                    continue

    return poses


def validate_pdb_file(path: Path) -> Tuple[bool, Optional[str]]:
    """Validate that a PDB file exists and has basic structural integrity.

    Args:
        path: Path to the PDB file

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not path.exists():
        return False, f"PDB file not found: {path}"

    if path.stat().st_size == 0:
        return False, f"PDB file is empty: {path}"

    # Check for basic PDB format
    has_atom_records = False
    with open(path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                has_atom_records = True
                break

    if not has_atom_records:
        return False, f"PDB file has no ATOM/HETATM records: {path}"

    return True, None


def validate_sdf_file(path: Path) -> Tuple[bool, Optional[str]]:
    """Validate that an SDF file exists and contains molecules.

    Args:
        path: Path to the SDF file

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not path.exists():
        return False, f"SDF file not found: {path}"

    if path.stat().st_size == 0:
        return False, f"SDF file is empty: {path}"

    # Check for at least one molecule delimiter
    has_molecules = False
    with open(path, 'r') as f:
        content = f.read()
        if '$$$$' in content:
            has_molecules = True

    if not has_molecules:
        return False, f"SDF file contains no molecules: {path}"

    return True, None


def run_command(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run a shell command and capture output.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory for the command

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out after 1 hour"
    except Exception as e:
        return -1, "", str(e)


def parse_resource_spec(spec: str) -> Dict[str, Any]:
    """Parse a resource specification string.

    Expected format: "cpus=4 mem=8G gpus=1 partition=gpu"

    Args:
        spec: Resource specification string

    Returns:
        Dict with resource keys and values
    """
    resources = {}

    parts = spec.split()
    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            # Try to convert to int if possible
            try:
                value = int(value.rstrip('G').rstrip('M'))
            except ValueError:
                pass
            resources[key] = value

    return resources
