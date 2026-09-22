"""Produce domain-independent daily/monthly forcing summaries on UTC bounds."""

import argparse
import json
from datetime import date
from pathlib import Path

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.temporal_summary import periods, summarize, summary_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frequency", choices=("daily", "monthly"), required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="Inclusive final date; monthly mode requires full months",
    )
    parser.add_argument(
        "--from-daily",
        action="store_true",
        help="Monthly only: read compatible daily summaries instead of hourly LDASIN",
    )
    parser.add_argument(
        "--reducers",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/forcing_daily_reducers.toml",
    )
    parser.add_argument(
        "--domain", default="", help="Provenance label; no geometry is inferred from it"
    )
    parser.add_argument("--stream", default="", help="Provenance label, e.g. nrt or retro")
    parser.add_argument("--block-rows", type=int, default=120)
    overwrite = parser.add_mutually_exclusive_group()
    overwrite.add_argument("--overwrite", action="store_true")
    overwrite.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip only when source identities and reducer settings match",
    )
    args = parser.parse_args()
    if args.from_daily and args.frequency != "monthly":
        parser.error("--from-daily requires --frequency monthly")
    reducers, names, units = load_forcing_reducers(args.reducers)
    for start, stop in periods(args.start, args.end, args.frequency):
        output = summary_path(args.output_root, start, monthly=args.frequency == "monthly")
        report = summarize(
            args.input_root,
            output,
            start,
            stop,
            reducers,
            names,
            units,
            from_daily=args.from_daily,
            block_rows=args.block_rows,
            overwrite=args.overwrite,
            skip_existing=args.skip_existing,
            domain=args.domain,
            stream=args.stream,
        )
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
