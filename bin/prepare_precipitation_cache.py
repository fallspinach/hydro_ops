"""Prepare a bounded job-local precipitation cache for an opt-in benchmark."""
import argparse
from datetime import date
from pathlib import Path

from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.precipitation_cache import prepare

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.start, args.days, OperationalLayout.project_defaults(Path(__file__).resolve().parents[1]), args.output, args.work)
