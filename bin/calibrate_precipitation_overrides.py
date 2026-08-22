#!/usr/bin/env python3
"""Fit regional Stage-IV override rules and score them once on withheld groups."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from hydro_ops.forcing.evaluation import (
    categorical_precipitation_metrics,
    continuous_metrics,
    deterministic_group_split,
    stage4_override_sweep,
)
from hydro_ops.forcing.precipitation import composite_precipitation


def _floats(text: str) -> list[float]:
    return [float(value) for value in text.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        required=True,
        type=Path,
        help="NPZ containing reference, quality, strata, group, MRMS and Stage-IV arrays",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calibration-fraction", type=float, default=0.7)
    parser.add_argument("--split-salt", default="hydro-ops-precipitation-v1")
    parser.add_argument("--quality-thresholds", type=_floats, default=_floats("0.2,0.4,0.5,0.6,0.8"))
    parser.add_argument(
        "--disagreement-thresholds", type=_floats, default=_floats("1,2,5,10,25")
    )
    args = parser.parse_args()
    with np.load(args.samples, allow_pickle=False) as samples:
        required = {"reference", "quality", "strata", "group", "mrms_pass2", "stage4_archive"}
        missing = required - set(samples.files)
        if missing:
            parser.error(f"samples are missing arrays: {sorted(missing)}")
        arrays = {name: samples[name] for name in required}
    calibration = deterministic_group_split(
        arrays["group"], calibration_fraction=args.calibration_fraction, salt=args.split_salt
    )
    candidates = {
        "mrms_pass2": arrays["mrms_pass2"],
        "stage4_archive": arrays["stage4_archive"],
    }
    sweep = stage4_override_sweep(
        candidates,
        arrays["quality"],
        arrays["reference"],
        arrays["strata"],
        quality_thresholds=args.quality_thresholds,
        disagreement_thresholds=args.disagreement_thresholds,
        calibration_mask=calibration,
    )
    rules: dict[str, dict] = {}
    withheld_reports: dict[str, dict] = {}
    for label in np.unique(arrays["strata"]):
        options = [row for row in sweep if row["stratum"] == str(label)]
        finite = [row for row in options if np.isfinite(row["metrics"]["rmse"])]
        if not finite:
            continue
        best = min(finite, key=lambda row: (row["metrics"]["rmse"], -row["categorical"]["critical_success_index"]))
        rules[str(label)] = {
            "quality_below": best["quality_below"],
            "absolute_disagreement_at_least": best["absolute_disagreement_at_least"],
            "calibration_metrics": best["metrics"],
            "calibration_categorical": best["categorical"],
        }
        region = arrays["strata"] == label
        override = (
            region
            & (
                (arrays["quality"] < best["quality_below"])
                | (
                    np.abs(arrays["stage4_archive"] - arrays["mrms_pass2"])
                    >= best["absolute_disagreement_at_least"]
                )
            )
        )
        composite = composite_precipitation(
            candidates, mrms_quality=arrays["quality"], stage4_override=override
        )
        selected = region & ~calibration
        withheld_reports[str(label)] = {
            "metrics": continuous_metrics(
                arrays["reference"][selected], composite.depth[selected]
            ).as_dict(),
            "categorical": categorical_precipitation_metrics(
                arrays["reference"][selected], composite.depth[selected]
            ).as_dict(),
            "count": int(selected.sum()),
        }
    report = {
        "created": datetime.now(UTC).isoformat(),
        "samples": str(args.samples),
        "split": {
            "method": "sha256 whole-group assignment",
            "salt": args.split_salt,
            "calibration_fraction": args.calibration_fraction,
            "calibration_count": int(calibration.sum()),
            "withheld_count": int((~calibration).sum()),
        },
        "selection_objective": "minimum calibration RMSE; CSI tie-break",
        "rules": rules,
        "withheld_validation": withheld_reports,
        "candidate_count": len(sweep),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f"{args.output.name}.part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=True) + "\n")
    partial.replace(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
