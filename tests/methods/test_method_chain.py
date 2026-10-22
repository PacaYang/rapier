"""Methods connect through the unified data types: one method's outputs feed the next."""

import pytest

from rapier.config import ConfigParser
from rapier.core.enums import TaskStatus
from rapier.core.pipeline import Pipeline
from rapier.core.task import Task
from rapier.data import Molecules, Poses, Scores
from rapier.engine.task_runner import TaskRunner
from rapier.methods.screening.molecule_filter import MoleculeFilter

BLOCK = "{name}\n  test\n\n  2  0  0  0  0  0  0  0  0  0999 V2000\n{atoms}M  END\n$$$$\n"
ATOM = "{x:10.4f}    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"


def _run(method, inputs, tmp_path, task_id):
    task = Task(task_id, method, inputs=inputs)
    task.work_dir = tmp_path / task_id
    assert method.execute(task) == TaskStatus.COMPLETED, task.error_message
    return task.outputs


@pytest.fixture
def docking(tmp_path):
    """Outputs shaped exactly like vina_docking's: Poses titled by id, Scores keyed by id/pose."""
    poses = [("a", 1, -9.0), ("a", 2, -6.0), ("b", 1, -8.0), ("c", 1, -5.0)]
    blocks = [BLOCK.format(name=name, atoms=ATOM.format(x=x) + ATOM.format(x=x + 1)) for name, _, x in poses]
    return {
        "docked_poses": Poses.write_sdf(blocks, tmp_path / "dock" / "poses.sdf"),
        "docking_scores": Scores.from_records(
            [{"id": name, "pose": pose, "affinity_kcal_mol": score} for name, pose, score in poses],
            tmp_path / "dock" / "scores.csv", directions={"affinity_kcal_mol": "lower"},
        ),
    }


def test_chained_gates_pair_rows_with_reordered_poses(tmp_path):
    """Poses re-ranked by the first gate must still join their own rows in the second."""
    blocks = [BLOCK.format(name="a", atoms=ATOM.format(x=x) + ATOM.format(x=x + 1)) for x in (0, 50)]
    poses = Poses.write_sdf(blocks, tmp_path / "in" / "poses.sdf")
    # Pose 2 scores better, so the first gate writes it first.
    scores = Scores.from_records([{"id": "a", "pose": 1, "affinity_kcal_mol": -5.0},
                                  {"id": "a", "pose": 2, "affinity_kcal_mol": -9.0}], tmp_path / "in" / "s.csv")
    gate = MoleculeFilter(config={"rank_by": "affinity_kcal_mol", "best_pose_only": False})
    first = _run(gate, {"molecules": poses, "scores": scores}, tmp_path, "gate1")
    second = _run(MoleculeFilter(config={"criteria": [{"column": "affinity_kcal_mol", "op": "<", "value": -8}]}),
                  {"molecules": first["filtered_molecules"], "scores": first["filtered_scores"]}, tmp_path, "gate2")
    kept = second["filtered_molecules"].records()
    assert len(kept) == 1 and kept[0].heavy_atoms[0][0] == 50.0


def test_docking_outputs_gate_twice_without_restating_direction(docking, tmp_path):
    gate = MoleculeFilter(config={"rank_by": "affinity_kcal_mol", "top_n": 2})
    first = _run(gate, {"molecules": docking["docked_poses"], "scores": docking["docking_scores"]}, tmp_path, "gate1")
    assert isinstance(first["filtered_molecules"], Poses)  # Poses stay Poses through the filter.
    assert first["filtered_molecules"].ids() == ["a", "b"]
    report = first["report"].load()
    assert report["gate"]["order"] == "ascending" and report["gate"]["order_source"] == "lower is better"
    # The filtered score table carries the direction on, so the next gate needs no `order`.
    assert first["filtered_scores"].directions(include_known=False)["affinity_kcal_mol"] == "lower"

    final = MoleculeFilter(config={"rank_by": "affinity_kcal_mol", "top_n": 1})
    second = _run(final, {"molecules": first["filtered_molecules"], "scores": first["filtered_scores"]},
                  tmp_path, "gate2")
    assert second["filtered_molecules"].ids() == ["a"]


def test_declared_direction_drives_auto_order(docking, tmp_path):
    rows = [{"id": "a", "ml_score": 0.2}, {"id": "b", "ml_score": 0.9}, {"id": "c", "ml_score": 0.5}]
    ml = Scores.from_records(rows, tmp_path / "ml.csv", directions={"ml_score": "higher"})
    gate = MoleculeFilter(config={"rank_by": "ml_score", "top_n": 1})
    out = _run(gate, {"molecules": docking["docked_poses"], "scores": [docking["docking_scores"], ml]},
               tmp_path, "gate")
    assert out["filtered_molecules"].ids() == ["b"]
    # Explicit order (or the legacy `ascending` key) still wins.
    legacy = MoleculeFilter(config={"rank_by": "ml_score", "top_n": 1, "ascending": True})
    assert legacy.config["order"] == "ascending"


def test_ligand_filter_properties_feed_the_gate(tmp_path, sample_data_dir):
    pytest.importorskip("rdkit")
    from rapier.methods.screening.ligand_filter import LigandFilter

    props = _run(LigandFilter(), {"ligands": str(sample_data_dir / "ligands.sdf")}, tmp_path, "lipinski")
    gate = MoleculeFilter(config={"criteria": [{"column": "tpsa", "op": ">", "value": 1}],
                                  "rank_by": "mw", "order": "descending", "top_n": 1})
    out = _run(gate, {"molecules": props["filtered_ligands"], "scores": props["properties"]}, tmp_path, "gate")
    assert out["filtered_molecules"].ids() == ["acetic_acid"]  # Benzene has no polar surface.


def test_prepared_smiles_feed_similarity_search(tmp_path):
    pytest.importorskip("rdkit")
    from rapier.methods.preparation.ligand_prep import LigandPreparation
    from rapier.methods.screening.similarity_search import SimilaritySearch

    prepared = _run(LigandPreparation(), {"ligands": ["CCO ethanol", "CCCO propanol", "c1ccccc1 benzene"]},
                    tmp_path, "prep")
    assert prepared["prepared_ligands"].ids() == ["ethanol", "propanol", "benzene"]
    search = SimilaritySearch(config={"similarity_threshold": 0.2})
    out = _run(search, {"queries": ["CCO query"], "database": prepared["prepared_ligands"]}, tmp_path, "sim")
    rows = out["similarity_scores"].records()
    assert rows[0]["id"] == "ethanol" and rows[0]["query_id"] == "query"
    assert "benzene" not in {row["id"] for row in rows}
    assert out["similarity_scores"].directions()["similarity"] == "higher"


def test_smina_outputs_match_vina_shape(tmp_path, sample_data_dir, monkeypatch):
    from rapier.methods.docking import smina

    def fake_smina(command, cwd=None):
        out = command[command.index("--out") + 1]
        with open(out, "w") as handle:
            handle.write(BLOCK.format(name="whatever", atoms=ATOM.format(x=0) + ATOM.format(x=1)))
        return 0, "mode |   affinity | dist from best mode\n-----+----\n1  -7.3  0.000  0.000\n", ""

    monkeypatch.setattr(smina, "run_command", fake_smina)
    monkeypatch.setattr(smina.SminaDocking, "validate_environment", lambda self: (True, []))
    out = _run(smina.SminaDocking(), {
        "receptor": str(sample_data_dir / "receptor.pdb"), "ligands": str(sample_data_dir / "ligands.sdf"),
        "binding_site": {"center": [0, 0, 0], "size": [20, 20, 20]},
    }, tmp_path, "smina")
    assert out["docked_poses"].ids() == ["ethanol", "benzene", "acetic_acid"]
    assert [(r["id"], r["pose"], r["affinity_kcal_mol"]) for r in out["docking_scores"].records()][0] == \
        ("ethanol", 1, "-7.3")
    assert out["docking_scores"].directions(include_known=False)["affinity_kcal_mol"] == "lower"


def test_protein_prep_returns_typed_receptor(tmp_path, sample_data_dir, monkeypatch):
    from rapier.data import ProteinStructure
    from rapier.methods.preparation import protein_prep

    def fake_obabel(command, cwd=None):
        with open(command[command.index("-O") + 1], "w") as handle:
            handle.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\n")
        return 0, "", ""

    monkeypatch.setattr(protein_prep, "run_command", fake_obabel)
    monkeypatch.setattr(protein_prep.ProteinPreparation, "validate_environment", lambda self: (True, []))
    out = _run(protein_prep.ProteinPreparation(), {"receptor": str(sample_data_dir / "receptor.pdb")}, tmp_path, "prep")
    assert isinstance(out["prepared_receptor"], ProteinStructure)
    assert out["prepared_receptor"].format == "pdbqt"
    assert out["preparation_log"].load()["steps"][-1] == "Converted to PDBQT with charges"


def test_yaml_pipeline_binds_properties_into_filter_scores(tmp_path, sample_data_dir):
    """A ligand_filter -> molecule_filter pipeline, staged by the engine's TaskRunner."""
    pytest.importorskip("rdkit")
    config = ConfigParser().parse_string(f"""
name: properties_gate
inputs:
  library: {{type: molecule.collection, format: sdf, source: {sample_data_dir / "ligands.sdf"}}}
methods:
  - id: lipinski
    type: ligand_filter
    inputs: {{ligands: pipeline.library}}
  - id: heaviest
    type: molecule_filter
    inputs: {{molecules: lipinski.filtered_ligands, scores: lipinski.properties}}
    config: {{rank_by: mw, order: descending, top_n: 1}}
""")
    pipeline = Pipeline.from_config(config)
    runner = TaskRunner(dry_run=True)
    for task_id in ("lipinski", "heaviest"):
        task = pipeline.tasks[task_id]
        task.work_dir = tmp_path / task_id
        runner.prepare_task(task)
        assert task.method.execute(task) == TaskStatus.COMPLETED, task.error_message
    assert isinstance(pipeline.tasks["heaviest"].inputs["molecules"], Molecules)
    assert pipeline.tasks["heaviest"].outputs["filtered_molecules"].ids() == ["benzene"]
