from pathlib import Path
from types import SimpleNamespace

from hydro_ops.work import temporary_work_root


def test_local_work_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_USER", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    settings = SimpleNamespace(work_root=tmp_path)
    assert temporary_work_root(settings, "prism") == tmp_path / "prism"


def test_slurm_scratch_path(monkeypatch):
    monkeypatch.setenv("SLURM_JOB_USER", "researcher")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setattr(Path, "mkdir", lambda self, **kwargs: None)
    settings = SimpleNamespace(work_root=Path("/unused"))
    assert temporary_work_root(settings, "prism") == Path("/scratch/researcher/job_12345/prism")


def test_slurm_scratch_falls_back_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_USER", "researcher")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    real_mkdir = Path.mkdir

    def mkdir(path, **kwargs):
        if str(path).startswith("/scratch/"):
            raise PermissionError("unavailable")
        return real_mkdir(path, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    settings = SimpleNamespace(work_root=tmp_path)
    assert temporary_work_root(settings, "prism") == tmp_path / "prism"
