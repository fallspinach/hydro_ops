"""Replay archived object-availability evidence for a delayed GFS cycle."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.download.gfs import GfsDownloader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "forcing/work/gfs-nrt-exploration"
    downloader = GfsDownloader(artifacts / "cache", args.work)
    valid = datetime(2026, 8, 24, 1, tzinfo=UTC)
    cutoff = datetime(2026, 8, 24, 2, tzinfo=UTC)
    result = downloader.hour(valid, as_of=cutoff)
    if result.attrs["lead"] != 7 or result.attrs["cycle"] != "2026-08-23T18:00:00+00:00":
        raise ValueError("Expected previous-cycle f007 under the archived availability cutoff")
    outage_rejected = False
    try:
        downloader.hour(valid, as_of=datetime(2026, 8, 23, 0, tzinfo=UTC))
    except RuntimeError:
        outage_rejected = True
    if not outage_rejected:
        raise ValueError("Impossible availability cutoff was not rejected")
    report = {"status": "passed", "valid_time": valid.isoformat(), "as_of": cutoff.isoformat(),
              "cycle": result.attrs["cycle"], "lead": int(result.attrs["lead"]),
              "outage_rejected": outage_rejected,
              "note": "Archive Last-Modified replay, not an observed real-time outage"}
    (artifacts / "cycle_fallback_check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
