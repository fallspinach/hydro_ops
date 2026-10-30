"""UCRB parameter extraction and isolated gated retro model entry point."""
import sys
from pathlib import Path

from backfill_ucrb_forcing import approved
from prepare_b120_domain import parameters
from run_b120_production import main as model_main

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    domain = root / 'nwm/static/domains/ucrb'
    approved(domain)
    if sys.argv[1:] == ['parameters']:
        parameters(root, domain, domain_name='ucrb', reporting_layer='ucrb_reporting')
    else:
        model_main(domain_name='ucrb')
