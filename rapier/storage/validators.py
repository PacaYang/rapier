"""File validation utilities for common molecular file formats."""

from pathlib import Path
from typing import List, Tuple, Optional


def validate_file_size(
    path: Path,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None
) -> bool:
    """Validate file size is within acceptable range.

    Args:
        path: File path
        min_size: Minimum size in bytes (None = no minimum)
        max_size: Maximum size in bytes (None = no maximum)

    Returns:
        True if file size is valid
    """
    if not Path(path).exists():
        return False

    size = Path(path).stat().st_size

    if min_size is not None and size < min_size:
        return False

    if max_size is not None and size > max_size:
        return False

    return True


def validate_sdf_file(path: Path) -> Tuple[bool, List[str]]:
    """Validate SDF (Structure-Data File) format.

    Args:
        path: File path

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.strip():
            errors.append("File is empty")
            return False, errors

        # SDF files should have molecule blocks ending with $$$$
        if '$$$$' not in content:
            errors.append("Missing molecule delimiter ($$$$)")

        # Check for required mol file components
        if 'M  END' not in content:
            errors.append("Missing molecule end marker (M  END)")

        # Count molecules
        molecule_count = content.count('$$$$')
        if molecule_count == 0:
            errors.append("No complete molecules found")

    except UnicodeDecodeError:
        errors.append("File is not valid UTF-8 text")
        return False, errors
    except Exception as e:
        errors.append(f"Error reading file: {e}")
        return False, errors

    is_valid = len(errors) == 0
    return is_valid, errors


def validate_pdb_file(path: Path) -> Tuple[bool, List[str]]:
    """Validate PDB (Protein Data Bank) format.

    Args:
        path: File path

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.strip():
            errors.append("File is empty")
            return False, errors

        lines = content.split('\n')

        # Check for ATOM or HETATM records
        has_atoms = any(
            line.startswith('ATOM  ') or line.startswith('HETATM')
            for line in lines
        )
        if not has_atoms:
            errors.append("No ATOM or HETATM records found")

        # Count coordinate lines
        atom_count = sum(
            1 for line in lines
            if line.startswith('ATOM  ') or line.startswith('HETATM')
        )
        if atom_count == 0:
            errors.append("No atoms found in file")

    except UnicodeDecodeError:
        errors.append("File is not valid UTF-8 text")
        return False, errors
    except Exception as e:
        errors.append(f"Error reading file: {e}")
        return False, errors

    is_valid = len(errors) == 0
    return is_valid, errors


def validate_csv_file(
    path: Path,
    expected_columns: Optional[List[str]] = None,
    delimiter: str = ','
) -> Tuple[bool, List[str]]:
    """Validate CSV file format.

    Args:
        path: File path
        expected_columns: Optional list of expected column names
        delimiter: Field delimiter (default: comma)

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    try:
        with open(path, 'r', encoding='utf-8') as f:
            # Read header
            header = f.readline().strip()

            if not header:
                errors.append("File is empty or has no header")
                return False, errors

            columns = [col.strip() for col in header.split(delimiter)]

            # Check for expected columns
            if expected_columns:
                missing = set(expected_columns) - set(columns)
                if missing:
                    errors.append(f"Missing expected columns: {', '.join(missing)}")

            # Check that there's at least one data row
            first_row = f.readline().strip()
            if not first_row:
                errors.append("No data rows found")

    except UnicodeDecodeError:
        errors.append("File is not valid UTF-8 text")
        return False, errors
    except Exception as e:
        errors.append(f"Error reading file: {e}")
        return False, errors

    is_valid = len(errors) == 0
    return is_valid, errors


def validate_file_not_empty(path: Path) -> Tuple[bool, List[str]]:
    """Validate that a file exists and is not empty.

    Args:
        path: File path

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    size = Path(path).stat().st_size
    if size == 0:
        errors.append("File is empty")
        return False, errors

    return True, []


def validate_file_readable(path: Path) -> Tuple[bool, List[str]]:
    """Validate that a file is readable.

    Args:
        path: File path

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    if not Path(path).is_file():
        errors.append("Path is not a file")
        return False, errors

    try:
        with open(path, 'r') as f:
            _ = f.read(1)
    except PermissionError:
        errors.append("File is not readable (permission denied)")
        return False, errors
    except Exception as e:
        errors.append(f"Cannot read file: {e}")
        return False, errors

    return True, []


def validate_json_file(path: Path) -> Tuple[bool, List[str]]:
    """Validate JSON file format.

    Args:
        path: File path

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    if not Path(path).exists():
        errors.append("File does not exist")
        return False, errors

    try:
        import json
        with open(path, 'r', encoding='utf-8') as f:
            json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {e}")
        return False, errors
    except Exception as e:
        errors.append(f"Error reading file: {e}")
        return False, errors

    return True, []


def get_file_format(path: Path) -> str:
    """Guess file format from extension.

    Args:
        path: File path

    Returns:
        Format string (sdf, pdb, csv, json, txt, unknown)
    """
    suffix = Path(path).suffix.lower()

    format_map = {
        '.sdf': 'sdf',
        '.mol': 'sdf',
        '.pdb': 'pdb',
        '.csv': 'csv',
        '.tsv': 'csv',
        '.json': 'json',
        '.txt': 'txt',
        '.log': 'txt',
    }

    return format_map.get(suffix, 'unknown')


def validate_file_by_format(path: Path, format: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Validate file based on its format.

    Args:
        path: File path
        format: Optional format override (auto-detected if None)

    Returns:
        Tuple of (is_valid, error_messages)
    """
    if format is None:
        format = get_file_format(path)

    validators = {
        'sdf': validate_sdf_file,
        'pdb': validate_pdb_file,
        'csv': validate_csv_file,
        'json': validate_json_file,
        'txt': validate_file_not_empty,
    }

    validator = validators.get(format, validate_file_not_empty)
    return validator(path)
