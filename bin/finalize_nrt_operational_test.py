"""Record accepted operational evidence and optionally push an unchanged checkpoint."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json


def summary(report):
    if (report.get("status") != "passed" or report.get("optimized_writers", {}).get("status") != "passed"
            or report.get("baseline_reuse") != "passed"):
        raise ValueError("Combined writer/reuse acceptance did not pass; do not push")
    first, repeat = report["first_cycle"], report["repeat_cycle"]
    if (first["status"] != "passed" or repeat["status"] != "passed" or first["errors"] or repeat["errors"]
            or not repeat["days"] or any(d["status"] != "unchanged" for d in repeat["days"])):
        raise ValueError("Cycle acceptance is incomplete")
    return ("# Combined NRT writer operational acceptance\n\n"
            f"Job {report['job_id']}: {report['start']} through {report['end']}.\n\n"
            "Both adopted writer profiles executed; baseline, PRISM-window and calendar\n"
            "writers used compressed chunks. Normal output validation passed. The unchanged\n"
            "repeat preserved both baseline and final file identities. Outputs were isolated.\n\n"
            f"- Cold worker time: {first['latency']['worker_seconds']:.1f} seconds.\n"
            f"- End-to-end time including queue/dependency wait: {first['latency']['launch_to_publication_seconds']:.1f} seconds.\n"
            f"- Unchanged repeat: {repeat['latency']['worker_seconds']:.1f} seconds.\n"
            f"- GFS target hours: {report['actual_gfs_hours']}.\n"
            f"- Cold worker under one hour: {report['first_worker_under_hour']}.\n\n"
            "Functional acceptance is separate from latency acceptance. This two-day test\n"
            "does not establish seven-day throughput or verify cron installation.\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to((root / "forcing/work").resolve())
    journal = campaign / "finalization.json"
    result = {"status": "checking", "expected_head": args.expected_head}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*command):
        return subprocess.check_output(["git", *command], cwd=root, env=env, text=True).strip()

    try:
        report = json.loads((campaign / "acceptance.json").read_text())
        content = summary(report) + f"\nEvidence: `{campaign.relative_to(root)}/acceptance.json`.\n"
        if git("rev-parse", "HEAD") != args.expected_head or git("branch", "--show-current") != "main":
            raise RuntimeError("Repository moved since test submission; manual review required")
        if git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Tracked/index changes present; do not commit or push unrelated work")
        if git("remote", "get-url", "origin") != "https://github.com/fallspinach/hydro_ops.git":
            raise RuntimeError("Unexpected Git remote")
        document = root / "docs/nrt_baseline_operational_acceptance.md"
        if document.exists():
            raise FileExistsError(document)
        document.write_text(content)
        git("add", str(document))
        git("commit", "--only", "-m", "Record combined NRT writer operational acceptance", "--", str(document))
        result["commit"] = git("rev-parse", "HEAD")
        result["status"] = "committed"
        _atomic_json(journal, result)
        if args.push:
            if git("status", "--porcelain", "--untracked-files=no"):
                raise RuntimeError("Worktree changed during finalization; push withheld")
            git("push", "origin", result["commit"] + ":refs/heads/main")
            result["status"] = "pushed"
    except Exception as error:
        result.update(status="blocked", error=str(error))
        raise
    finally:
        _atomic_json(journal, result)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
