"""Executable workflow used by :class:`VinaDocking`.

The scheduler executes a command, not a Python method object.  Keeping the
workflow here gives local execution and SLURM execution exactly the same
preparation, docking, and collection behavior.  The implementation uses the
official AutoDock Vina command line tools and Meeko's preparation/export
scripts; Open Babel is deliberately not used for PDBQT round-tripping.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...data import molecule_id, sdf_terminated, split_sdf as split_sdf_text
from ..utils import parse_vina_output, run_command


def _safe_name(name: str, fallback: str) -> str:
    """Return a stable filename component (and join id) for an SDF molecule name."""
    return molecule_id(name, fallback)


def _retitle(payload: str, title: str) -> str:
    """Pose records titled with the ligand id, so poses join with score rows."""
    return "".join(
        sdf_terminated("\n".join([title] + block.splitlines()[1:]) + "\n")
        for block in split_sdf_text(payload)
    )


def _unique_name(name: str, used: Dict[str, int]) -> str:
    """Make names unique even when a source name already has an index suffix."""
    count = used.get(name, 0)
    candidate = name if count == 0 else f"{name}-{count + 1}"
    while candidate in used:
        count += 1
        candidate = f"{name}-{count + 1}"
    used[name] = count + 1
    used[candidate] = 1
    return candidate


def split_sdf(path: Path, output_dir: Path) -> List[Dict[str, Any]]:
    """Split an SDF collection into one record per ligand.

    Meeko's ligand preparation command is intentionally invoked once per
    molecule.  Splitting on the SDF record delimiter avoids requiring RDKit in
    the execution environment and preserves the input molecule text exactly.
    """
    text = path.read_text()
    records: List[Dict[str, Any]] = []
    used: Dict[str, int] = {}
    for index, raw in enumerate(text.split("$$$$")):
        if not raw.strip():
            continue
        stripped_raw = raw.lstrip()
        record = stripped_raw.rstrip() + "\n$$$$\n"
        first_line = stripped_raw.splitlines()[0].strip() if stripped_raw.splitlines() else ""
        stem = _unique_name(_safe_name(first_line, f"ligand_{index}"), used)
        sdf_path = output_dir / f"{stem}.sdf"
        sdf_path.write_text(record)
        records.append({"index": index, "name": stem, "sdf": str(sdf_path)})
    return records


def _write_box_config(path: Path, box: Dict[str, Iterable[float]]) -> None:
    center = list(box["center"])
    size = list(box["size"])
    path.write_text(
        "center_x = {0}\ncenter_y = {1}\ncenter_z = {2}\n"
        "size_x = {3}\nsize_y = {4}\nsize_z = {5}\n".format(
            *center, *size
        )
    )


def _parse_scores(text: str) -> List[Dict[str, Any]]:
    scores = parse_vina_output(text)
    if scores:
        return scores
    # Some Vina builds only emit the mode table in a log file.  Also accept
    # the REMARK form embedded in PDBQT output as a useful fallback.
    fallback: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"REMARK\s+VINA RESULT:\s*([-+]?\d+(?:\.\d+)?)\s+"
        r"([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)"
    )
    for mode, match in enumerate(pattern.finditer(text), start=1):
        fallback.append(
            {
                "mode": mode,
                "affinity": float(match.group(1)),
                "rmsd_lb": float(match.group(2)),
                "rmsd_ub": float(match.group(3)),
            }
        )
    return fallback


def _run_tool(command: List[str], cwd: Path) -> Tuple[int, str, str]:
    """Small indirection that makes command execution easy to mock in tests."""
    return run_command(command, cwd=cwd)


def _prepare_receptor(manifest: Dict[str, Any], work_dir: Path, prepared_dir: Path) -> Path:
    receptor = Path(manifest["receptor"])
    receptor_pdbqt = prepared_dir / "receptor.pdbqt"
    if receptor.suffix.lower() == ".pdbqt":
        shutil.copy2(receptor, receptor_pdbqt)
        return receptor_pdbqt

    if receptor.suffix.lower() in (".cif", ".mmcif"):
        # Meeko's receptor helper consumes PDB coordinates.  Convert CIF at
        # the task boundary with Gemmi when a CIF artifact reaches Vina.
        converted = prepared_dir / "receptor.pdb"
        try:
            import gemmi

            gemmi.read_structure(str(receptor)).write_pdb(str(converted))
            receptor = converted
        except ImportError as exc:
            raise RuntimeError(
                "CIF receptor input requires the optional 'gemmi' package"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"CIF receptor conversion failed: {exc}") from exc

    prefix = prepared_dir / "receptor"
    box = manifest["box"]
    command = [
        "mk_prepare_receptor.py",
        "-i",
        str(receptor),
        "-o",
        str(prefix),
        "-p",
        "-v",
        "--box_center",
        *[str(value) for value in box["center"]],
        "--box_size",
        *[str(value) for value in box["size"]],
    ]
    returncode, stdout, stderr = _run_tool(command, work_dir)
    if returncode != 0:
        raise RuntimeError(f"Receptor preparation failed: {stderr or stdout}")
    if not receptor_pdbqt.exists():
        candidates = list(prepared_dir.glob("*.pdbqt"))
        if len(candidates) == 1:
            candidates[0].replace(receptor_pdbqt)
    if not receptor_pdbqt.exists():
        raise RuntimeError(
            "Receptor preparation completed without producing receptor.pdbqt"
        )
    return receptor_pdbqt


def run_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Run a Vina task described by a JSON manifest.

    The returned summary is also written to ``outputs/vina_run.json``.  A
    ``RuntimeError`` is raised only when no ligand produced a usable pose, so
    callers can collect partial results from successful virtual-screening
    batches while still failing an empty run.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    work_dir = Path(manifest["work_dir"])
    prepared_dir = work_dir / "vina" / "prepared"
    ligand_dir = work_dir / "vina" / "ligands"
    pose_dir = work_dir / "vina" / "poses"
    output_dir = work_dir / "outputs"
    for directory in (prepared_dir, ligand_dir, pose_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    receptor_pdbqt = _prepare_receptor(manifest, work_dir, prepared_dir)
    config = {
        "scoring": "vina",
        "exhaustiveness": 8,
        "num_modes": 9,
        "energy_range": 3,
        "verbosity": 1,
        "cpu": None,
        "seed": None,
        "min_rmsd": None,
        "allow_partial_failures": True,
        **dict(manifest["config"]),
    }
    box_config = prepared_dir / "receptor.box.txt"
    _write_box_config(box_config, manifest["box"])

    ligand_records = split_sdf(Path(manifest["ligands"]), ligand_dir)
    if not ligand_records:
        raise RuntimeError("Ligand input contains no SDF records")

    results: List[Dict[str, Any]] = []
    for ligand in ligand_records:
        result: Dict[str, Any] = {
            "index": ligand["index"],
            "ligand": ligand["name"],
            "input": ligand["sdf"],
            "success": False,
            "scores": [],
        }
        prepared_ligand = prepared_dir / f"{ligand['name']}.pdbqt"
        output_pdbqt = pose_dir / f"{ligand['name']}_out.pdbqt"
        output_sdf = pose_dir / f"{ligand['name']}_out.sdf"
        log_file = pose_dir / f"{ligand['name']}.log"
        try:
            command = [
                "mk_prepare_ligand.py",
                "-i",
                ligand["sdf"],
                "-o",
                str(prepared_ligand),
            ]
            returncode, stdout, stderr = _run_tool(command, work_dir)
            if returncode != 0 or not prepared_ligand.exists():
                raise RuntimeError(f"Ligand preparation failed: {stderr or stdout}")

            vina_command = [
                "vina",
                "--receptor",
                str(receptor_pdbqt),
                "--ligand",
                str(prepared_ligand),
                "--config",
                str(box_config),
                "--out",
                str(output_pdbqt),
                "--log",
                str(log_file),
                "--scoring",
                str(config["scoring"]),
                "--exhaustiveness",
                str(config["exhaustiveness"]),
                "--num_modes",
                str(config["num_modes"]),
                "--energy_range",
                str(config["energy_range"]),
                "--verbosity",
                str(config["verbosity"]),
            ]
            if config.get("cpu") is not None:
                vina_command.extend(["--cpu", str(config["cpu"])])
            if config.get("seed") is not None:
                vina_command.extend(["--seed", str(config["seed"])])
            if config.get("min_rmsd") is not None:
                vina_command.extend(["--min_rmsd", str(config["min_rmsd"])])
            returncode, stdout, stderr = _run_tool(vina_command, work_dir)
            # Some packaged Vina builds (including older conda-forge builds)
            # do not expose the optional ``--log`` flag even though they
            # support the same docking arguments.  Retry once without it and
            # continue to parse scores from stdout/stderr and the output PDBQT.
            if returncode != 0 and "unrecognised option '--log'" in (stderr or "").lower():
                fallback_command = list(vina_command)
                log_index = fallback_command.index("--log")
                del fallback_command[log_index:log_index + 2]
                returncode, stdout, stderr = _run_tool(fallback_command, work_dir)
            if returncode != 0 or not output_pdbqt.exists():
                raise RuntimeError(f"Vina docking failed: {stderr or stdout}")

            log_text = log_file.read_text() if log_file.exists() else ""
            scores = []
            # Prefer the first complete report.  Vina builds may write the
            # same mode table to stdout and --log; concatenating both would
            # duplicate every score row.
            for report in (stdout, stderr, log_text, output_pdbqt.read_text()):
                scores = _parse_scores(report)
                if scores:
                    break
            if not scores:
                raise RuntimeError("Vina completed without a parseable score table")

            export_command = ["mk_export.py", str(output_pdbqt), "-s", str(output_sdf)]
            returncode, export_stdout, export_stderr = _run_tool(export_command, work_dir)
            if returncode != 0 or not output_sdf.exists():
                raise RuntimeError(
                    f"Pose export failed: {export_stderr or export_stdout}"
                )
            result.update(
                {
                    "success": True,
                    "scores": scores,
                    "pdbqt": str(output_pdbqt),
                    "sdf": str(output_sdf),
                }
            )
        except Exception as exc:
            result["error"] = str(exc)
        results.append(result)
        if result.get("error") and not config.get("allow_partial_failures", True):
            break

    successful = [item for item in results if item["success"]]
    failed = [item for item in results if not item["success"]]
    combined_sdf = output_dir / "docked_poses.sdf"
    with combined_sdf.open("w") as handle:
        for item in successful:
            handle.write(_retitle(Path(item["sdf"]).read_text(), item["ligand"]))

    # Canonical score table: one row per pose, keyed by ligand id and pose rank.
    scores_csv = output_dir / "docking_scores.csv"
    with scores_csv.open("w", newline="") as handle:
        fieldnames = [
            "id",
            "pose",
            "affinity_kcal_mol",
            "rmsd_lb",
            "rmsd_ub",
            "scoring_function",
            "pose_file",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in successful:
            for score in item["scores"]:
                writer.writerow(
                    {
                        "id": item["ligand"],
                        "pose": score["mode"],
                        "affinity_kcal_mol": score["affinity"],
                        "rmsd_lb": score["rmsd_lb"],
                        "rmsd_ub": score["rmsd_ub"],
                        "scoring_function": config["scoring"],
                        "pose_file": item["sdf"],
                    }
                )

    summary = {
        "status": "completed" if successful and not (failed and not config.get("allow_partial_failures", True)) else "failed",
        "receptor": str(receptor_pdbqt),
        "ligands": results,
        "successful_ligands": len(successful),
        "failed_ligands": [item["ligand"] for item in failed],
        "outputs": {"docked_poses": str(combined_sdf), "docking_scores": str(scores_csv)},
    }
    summary_path = output_dir / "vina_run.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    if not successful or (failed and not config.get("allow_partial_failures", True)):
        errors = "; ".join(
            f"{item['ligand']}: {item.get('error', 'unknown failure')}" for item in failed
        )
        prefix = "All ligands failed" if not successful else "Vina run failed due to ligand failures"
        raise RuntimeError(f"{prefix}: {errors}")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Rapier AutoDock Vina manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run_manifest(args.manifest)
    except Exception as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by scheduler
    raise SystemExit(main())
