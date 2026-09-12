"""Determine hourly source identity from cell provenance, never inherited labels."""

import json

import numpy as np


def classify_hour(ids):
    values = set(np.unique(np.ma.filled(ids, 0)).tolist()) - {0}
    if not values or not values <= {1, 2, 3, 4}:
        raise ValueError(f"Unknown or empty hourly source provenance: {values}")
    if values <= {1, 3}:
        return "nldas2" if values == {1} else "nldas2_hrrr_hybrid"
    if values == {2}:
        return "hrrr"
    return "mixed"


def refresh_source_summary(data):
    modes = [classify_hour(data["forcing_source_id"][i]) for i in range(len(data.dimensions["time"]))]
    data.forcing_source_by_hour = json.dumps(modes)
    data.forcing_source = modes[0] if len(set(modes)) == 1 else "mixed"
    if "forcing_source_id" in data.ncattrs():
        data.delncattr("forcing_source_id")
    return modes
