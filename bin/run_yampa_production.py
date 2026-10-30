"""Yampa parameter extraction and gated, isolated retrospective model runs."""
import json
import sys
from pathlib import Path

from prepare_b120_domain import digest, parameters
from run_b120_production import main as model_main


def approved(root):
    domain = root / 'nwm/static/domains/yampa'
    record = json.loads((domain / 'manifest.json').read_text())
    if (record.get('status') != 'passed' or record.get('domain') != 'yampa'
            or record.get('approved_for_production') is not True
            or record.get('mask_sha256') != digest(domain / 'masks/yampa_masks.nc')
            or record.get('boundary_sha256') != digest(domain / 'boundaries/yampa_boundaries.gpkg')):
        raise ValueError('Yampa geometry/mask approval is missing or changed')
    return domain


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    domain = approved(root)
    if sys.argv[1:] == ['parameters']:
        parameters(root, domain, domain_name='yampa', reporting_layer='yampa_reporting')
    else:
        model_main(domain_name='yampa')
