"""Integration tests for file management (staging, collection, cleanup)."""

import pytest

from rapier.storage.structure import ProjectStructure
from rapier.storage.path_manager import PathManager
from rapier.storage.file_tracker import FileTracker
from rapier.core.specs import InputSpec, OutputSpec, PortSpec
from rapier.data import File, Molecules
from rapier.storage.file_organizer import FileOrganizer
from rapier.storage.cleanup_manager import CleanupManager, CleanupPolicy, POLICIES
from rapier.core.task import Task


@pytest.fixture
def project(tmp_path):
    """A created project structure under tmp_path."""
    return ProjectStructure.create_project(str(tmp_path / "proj"))


@pytest.fixture
def path_manager(project):
    pm = PathManager(str(project.root), run_id="20260101_000000")
    pm.ensure_run_structure()
    return pm


class TestProjectStructure:
    def test_create_project(self, project):
        assert project.config_dir.exists()
        assert project.input_dir.exists()
        assert project.work_dir.exists()
        assert (project.input_dir / "receptors").exists()

    def test_validate_structure(self, project):
        is_valid, missing = project.validate_structure()
        assert is_valid
        assert missing == []

    def test_create_run_directory(self, project):
        run_dir = project.create_run_directory(timestamp="20260101_120000")
        assert run_dir.exists()
        assert (run_dir / "tasks").exists()

    def test_get_directory_invalid(self, project):
        with pytest.raises(ValueError):
            project.get_directory("nonsense")


class TestPathManager:
    def test_run_id_normalization(self, project):
        pm = PathManager(str(project.root), run_id="abc")
        assert pm.run_id == "run_abc"
        pm2 = PathManager(str(project.root), run_id="run_abc")
        assert pm2.run_id == "run_abc"

    def test_task_dirs(self, path_manager):
        path_manager.ensure_task_structure("task1")
        assert path_manager.get_task_input_dir("task1").exists()
        assert path_manager.get_task_output_dir("task1").exists()

    def test_resolve_input_absolute(self, path_manager, tmp_path):
        abs_path = tmp_path / "file.txt"
        resolved = path_manager.resolve_input_path(str(abs_path))
        assert resolved.is_absolute()

    def test_is_under_project(self, path_manager):
        inside = path_manager.input_dir / "x.txt"
        assert path_manager.is_under_project(inside)
        assert not path_manager.is_under_project("/etc/passwd")


class TestFileStaging:
    def test_stage_inputs_copies(self, path_manager, project):
        # Create a source input file
        source = project.input_dir / "ligands.sdf"
        source.write_text("molecule data")

        tracker = FileTracker()
        organizer = FileOrganizer(path_manager, tracker)

        task = Task(task_id="dock", method=None, inputs={"ligands": str(source)})
        path_manager.ensure_task_structure("dock")

        # use_symlinks=False forces a copy
        staged = organizer.stage_inputs(task, InputSpec({"ligands": PortSpec(Molecules, "sdf")}), use_symlinks=False)
        assert "ligands" in staged
        assert staged["ligands"].exists()
        assert staged["ligands"].read_text() == "molecule data"
        # not a symlink since we copied
        assert not staged["ligands"].is_symlink()

    def test_stage_inputs_symlinks(self, path_manager, project):
        source = project.input_dir / "big.dat"
        source.write_text("data")

        organizer = FileOrganizer(path_manager)
        task = Task(task_id="t", method=None, inputs={"big": str(source)})
        path_manager.ensure_task_structure("t")

        staged = organizer.stage_inputs(task, InputSpec({"big": PortSpec(File)}), use_symlinks=True)
        # Files under inputs/ should be symlinked
        assert staged["big"].is_symlink()

    def test_stage_missing_input_raises(self, path_manager):
        organizer = FileOrganizer(path_manager)
        task = Task(task_id="t", method=None, inputs={"x": "/no/such/file.sdf"})
        path_manager.ensure_task_structure("t")
        with pytest.raises(FileNotFoundError):
            organizer.stage_inputs(task, InputSpec({"x": PortSpec(Molecules, "sdf")}), use_symlinks=False)

    def test_collect_outputs(self, path_manager):
        organizer = FileOrganizer(path_manager)
        path_manager.ensure_task_structure("t")

        # Write an output file
        out_dir = path_manager.get_task_output_dir("t")
        (out_dir / "results.sdf").write_text("results")

        task = Task(task_id="t", method=None, inputs={})
        collected = organizer.collect_outputs(task, OutputSpec({"results": PortSpec(Molecules, "sdf")}))
        assert "results.sdf" in collected
        assert collected["results.sdf"].exists()


class TestCleanup:
    def test_cleanup_policies_exist(self):
        assert "minimal" in POLICIES
        assert "standard" in POLICIES
        assert "aggressive" in POLICIES

    def test_standard_policy_drops_intermediates(self):
        policy = POLICIES["standard"]
        assert policy.should_keep("input")
        assert policy.should_keep("output")
        assert not policy.should_keep("intermediate")

    def test_cleanup_task_keeps_outputs(self, path_manager):
        tracker = FileTracker()
        path_manager.ensure_task_structure("t")

        # Create an intermediate and an output file
        inter_dir = path_manager.get_task_dir("t")
        inter = inter_dir / "scratch.tmp"
        inter.write_text("scratch")
        out = path_manager.get_task_output_dir("t") / "final.sdf"
        out.write_text("final")

        tracker.register_file(inter, category="intermediate", task_id="t")
        tracker.register_file(out, category="output", task_id="t")
        tracker.mark_important(out)

        manager = CleanupManager(tracker, path_manager)
        stats = manager.cleanup_task("t", keep_outputs=True)

        # Intermediate removed, output preserved
        assert not inter.exists()
        assert out.exists()
        assert stats["deleted_files"] >= 1

    def test_cleanup_dry_run(self, path_manager):
        tracker = FileTracker()
        path_manager.ensure_task_structure("t")
        inter = path_manager.get_task_dir("t") / "scratch.tmp"
        inter.write_text("scratch")
        tracker.register_file(inter, category="intermediate", task_id="t")

        manager = CleanupManager(tracker, path_manager)
        stats = manager.cleanup_task("t", dry_run=True)
        # dry-run does not delete
        assert inter.exists()

    def test_mark_preserve(self, path_manager):
        tracker = FileTracker()
        path_manager.ensure_task_structure("t")
        f = path_manager.get_task_dir("t") / "keep.tmp"
        f.write_text("x")
        tracker.register_file(f, category="intermediate", task_id="t")

        manager = CleanupManager(tracker, path_manager)
        manager.mark_preserve(f)
        manager.cleanup_task("t", keep_outputs=False)
        assert f.exists()


class TestFileTracker:
    def test_register_and_query(self):
        tracker = FileTracker()
        from pathlib import Path
        tracker.register_file(Path("/a/b.txt"), category="output", task_id="t1")
        assert len(tracker.get_files_by_category("output")) == 1
        assert len(tracker.get_files_by_task("t1")) == 1

    def test_important_flag(self):
        from pathlib import Path
        tracker = FileTracker()
        p = Path("/a/b.txt")
        tracker.register_file(p, category="output")
        tracker.mark_important(p)
        assert tracker.is_important(p)

    def test_manifest_roundtrip(self, tmp_path):
        from pathlib import Path
        tracker = FileTracker()
        tracker.register_file(Path("/a/b.txt"), category="output", task_id="t1")
        tracker.mark_important(Path("/a/b.txt"))

        manifest = tmp_path / "manifest.json"
        tracker.save_manifest(str(manifest))

        restored = FileTracker.load_manifest(str(manifest))
        assert len(restored.get_files_by_category("output")) == 1
        assert restored.is_important(Path("/a/b.txt"))
