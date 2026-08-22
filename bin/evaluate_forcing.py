#!/usr/bin/env python3
"""Compute reproducible continuous and optional stratified forcing metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import xarray as xr

from hydro_ops.forcing.evaluation import continuous_metrics, stratified_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-variable", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-variable", required=True)
    parser.add_argument("--strata", type=Path)
    parser.add_argument("--strata-variable")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with (
        xr.open_dataset(args.reference, mask_and_scale=True) as reference,
        xr.open_dataset(args.candidate, mask_and_scale=True) as candidate,
    ):
        reference_values = reference[args.reference_variable].values
        candidate_values = candidate[args.candidate_variable].values
    report = {
        "reference": str(args.reference),
        "candidate": str(args.candidate),
        "overall": continuous_metrics(reference_values, candidate_values).as_dict(),
    }
    if args.strata is not None:
        if args.strata_variable is None:
            parser.error("--strata-variable is required with --strata")
        with xr.open_dataset(args.strata, mask_and_scale=True) as strata:
            grouped = stratified_metrics(
                reference_values, candidate_values, strata[args.strata_variable].values
            )
        report["strata"] = {name: metrics.as_dict() for name, metrics in grouped.items()}
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_name(f"{args.output.name}.part")
        partial.write_text(text)
        partial.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
