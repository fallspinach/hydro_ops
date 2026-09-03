#!/usr/bin/env python3
"""Print a canonical 00-UTC WRF-Hydro operational run plan."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from hydro_ops.wrf_hydro.run_plan import plan_operational_run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--hours", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(plan_operational_run(args.start, args.hours).as_dict(), indent=2))


if __name__ == "__main__":
    main()
