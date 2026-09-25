import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin/run_cron.sh"


def test_relocated_wrapper_and_cron_render(tmp_path):
    moved = tmp_path / "relocated project"
    (moved / "bin").mkdir(parents=True)
    (moved / "config").mkdir()
    for name in ("run_cron.sh", "project_environment.sh"):
        shutil.copy2(ROOT / "bin" / name, moved / "bin" / name)
    shutil.copy2(ROOT / "config/site.env", moved / "config/site.env")
    (moved / "config/project.toml").write_text("[paths]\n")
    # Portable fixture: use the interpreter running this test, with dummy SLURM
    # clients sufficient for wrapper command discovery. No scheduler query occurs.
    import sys

    fake = tmp_path / "slurm"
    (fake / "bin").mkdir(parents=True)
    for name in ("squeue", "sbatch", "sacct"):
        tool = fake / "bin" / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    conf = tmp_path / "slurm.conf"
    conf.touch()
    env = {
        "PATH": "/usr/bin:/bin",
        "HYDRO_OPS_ENV_BIN": str(Path(sys.executable).parent),
        "HYDRO_OPS_SLURM_ROOT": str(fake),
        "HYDRO_OPS_SLURM_CONF": str(conf),
    }
    result = subprocess.run(
        [
            "/usr/bin/bash",
            str(moved / "bin/run_cron.sh"),
            "-c",
            'import os; print(os.environ["HYDRO_OPS_PROJECT_ROOT"])',
        ],
        env=env,
        cwd="/tmp",
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.strip() == str(moved)
    env["HYDRO_OPS_PROJECT_ROOT"] = str(tmp_path / "absent")
    result = subprocess.run(
        ["/usr/bin/bash", str(moved / "bin/run_cron.sh"), "--check"],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0 and "Invalid HYDRO_OPS_PROJECT_ROOT" in result.stderr
    spec = importlib.util.spec_from_file_location("render_cron", ROOT / "bin/render_crontab.py")
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    template = (ROOT / "cron/hydro_ops.crontab.in").read_text()
    rendered = renderer.render(moved, template)
    assert "'" + str(moved / "bin/run_cron.sh") + "'" in rendered
    assert str(ROOT) not in rendered
    assert "@WRAPPER@" not in rendered
    with pytest.raises(ValueError, match="percent"):
        renderer.render(tmp_path / "bad%path", template)
    current = (ROOT / "cron/hydro_ops.crontab").read_text()
    entries = lambda text: [line for line in text.splitlines() if line and line[0].isdigit()]
    assert entries(renderer.render(ROOT, template)) == entries(current)


def test_cron_template_uses_wrapper():
    entries = [
        line
        for line in (ROOT / "cron/hydro_ops.crontab").read_text().splitlines()
        if line and line[0].isdigit()
    ]
    assert len(entries) == 4
    assert all("bin/run_cron.sh " in line and "conda run" not in line for line in entries)
    assert all(">> /cw3e/" in line for line in entries)


def test_nrt_cron_utc_schedule():
    for name in ("hydro_ops.crontab", "hydro_ops.crontab.in"):
        text = (ROOT / "cron" / name).read_text()
        assert "CRON_TZ=UTC" in text.splitlines()
        entries = [line for line in text.splitlines() if line and line[0].isdigit()]
        daily = [line for line in entries if "--cycle daily " in line]
        fast = [line for line in entries if "--cycle six-hourly " in line]
        assert len(daily) == len(fast) == 1
        assert daily[0].split()[:5] == ["30", "2", "*", "*", "*"]
        assert fast[0].split()[:5] == ["30", "8,14,20", "*", "*", "*"]


@pytest.mark.skipif(
    not Path("/cm/shared/apps/slurm/current/bin/squeue").exists(),
    reason="AWARE login-node integration test",
)
def test_clean_environment_and_argument_forwarding():
    code = (
        "import json,os,shutil,sys; print(json.dumps(dict(args=sys.argv[1:], "
        'cwd=os.getcwd(), python=sys.executable, squeue=shutil.which("squeue"), '
        'threads=os.environ["OMP_NUM_THREADS"])))'
    )
    result = subprocess.run(
        ["/usr/bin/bash", str(WRAPPER), "-c", code, "argument with spaces"],
        env={"PATH": "/usr/bin:/bin"},
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    record = json.loads(result.stdout)
    assert record["args"] == ["argument with spaces"]
    assert record["cwd"] == str(ROOT)
    assert record["python"].endswith("/envs/hydro-ops/bin/python")
    assert record["squeue"] == "/cm/shared/apps/slurm/current/bin/squeue"
    assert record["threads"] == "1"
    assert "fi_info" not in result.stderr
    failure = subprocess.run(
        ["/usr/bin/bash", str(WRAPPER), "-c", "raise SystemExit(7)"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert failure.returncode == 7
