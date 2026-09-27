"""Cron/manual entry point for CNRFC hourly, daily and monthly propagation."""
import argparse
from pathlib import Path

from hydro_ops.forcing.cnrfc_sync import submit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', choices=('all', 'nrt', 'retro'), default='all')
    parser.add_argument('--frequency', choices=('all', 'hourly', 'daily', 'monthly'), default='all')
    args = parser.parse_args()
    submit(Path(__file__).resolve().parents[1], stream=args.stream, frequency=args.frequency)


if __name__ == '__main__':
    main()
