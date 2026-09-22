"""Render a relocated cron schedule to stdout; never installs a crontab."""

import argparse
import shlex
from pathlib import Path


def render(root: Path, template: str) -> str:
    root = root.expanduser().resolve()
    # Cron treats percent specially even inside shell quotes. Fail closed rather
    # than emit an ambiguous command; newlines cannot be embedded in cron entries.
    if any(c in str(root) for c in ("%", "\n", "\r", "\x00")):
        raise ValueError("Cron project paths cannot contain percent or newline characters")
    return template.replace("@WRAPPER@", shlex.quote(str(root / "bin/run_cron.sh"))).replace(
        "@LOG_ROOT@", shlex.quote(str(root / "forcing/logs"))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    template = Path(__file__).resolve().parents[1] / "cron/hydro_ops.crontab.in"
    print(render(args.project_root, template.read_text()), end="")


if __name__ == "__main__":
    main()
