"""Rename summary products only; never changes hourly archives or NetCDF data."""

import argparse
import fcntl
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Domain/stream root")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    with (args.root / ".summary-backfill.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        moves = []
        for frequency, pattern, stamp in [
            ("daily", "*/*/*.FORCING_DAILY.nc", r"\d{8}"),
            ("monthly", "*/*.FORCING_MONTHLY.nc", r"\d{6}"),
        ]:
            for source in sorted((args.root / frequency).glob(pattern)):
                if source.is_symlink() or not re.fullmatch(
                    stamp + r"\.FORCING_" + frequency.upper() + r"\.nc", source.name
                ):
                    raise ValueError(f"Unexpected source: {source}")
                destination = source.with_name(
                    source.name.split(".")[0] + ".LDASIN_DOMAIN1." + frequency
                )
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(destination)
                moves.append((source, destination))
        for source, destination in moves:
            if args.execute:
                source.rename(destination)
            print(
                json.dumps(
                    {
                        "source": str(source),
                        "destination": str(destination),
                        "executed": args.execute,
                    }
                ),
                flush=True,
            )
        print(
            json.dumps({"status": "completed" if args.execute else "planned", "files": len(moves)})
        )


if __name__ == "__main__":
    main()
