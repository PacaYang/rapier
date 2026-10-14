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


# Score-column semantics shared by the docking methods' ``docking_scores`` tables.
DOCKING_SCORE_COLUMNS = {
    'affinity_kcal_mol': {'better': 'lower', 'unit': 'kcal/mol'},
    'rmsd_lb': {'unit': 'A'},
    'rmsd_ub': {'unit': 'A'},
}


def rdkit_molecules(molecules: Any) -> List[Tuple[Any, Any]]:
    """``(MoleculeRecord, rdkit Mol or None)`` for every record of a molecule artifact.

    SDF data properties are copied onto the RDKit molecule and ``_Name`` is the
    record name, so molecules written back out keep their id.
    """
    from rdkit import Chem

    result = []
    for record in molecules.records():
        if record.smiles is not None:
            mol = Chem.MolFromSmiles(record.smiles)
        else:
            mol = Chem.MolFromMolBlock(record.block)
        if mol is not None:
            for key, value in record.properties.items():
                mol.SetProp(key, value)
            mol.SetProp('_Name', record.name)
        result.append((record, mol))
    return result


def write_rdkit_sdf(molecules: List[Any], path: Path) -> Path:
    """Write RDKit molecules (with their properties) to an SDF file."""
    from rdkit import Chem

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(path))
    for mol in molecules:
        writer.write(mol)
    writer.close()
    return path


def resolve_box(site: Any, center: Any, size: Any) -> Dict[str, List[float]]:
    """Docking box from a binding_site input, else from deprecated box_center/box_size config."""
    if site is not None:
        return site.box()
    for label, value in (('box_center', center), ('box_size', size)):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"{label} must be a list of 3 numbers [x, y, z]")
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            raise ValueError(f"{label} must contain only numbers")
    if any(item <= 0 for item in size):
        raise ValueError("box_size dimensions must be positive")
    return {'center': [float(item) for item in center], 'size': [float(item) for item in size]}


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
