"""Stable precipitation provenance schema for staged forcing products."""

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import SOURCE_IDS


def ensure_precipitation_timing(path):
    """Add a zero timing-source field when no six-hour reconciliation created one.

    This changes no physical field and never replaces existing timing provenance.
    Call on owned hourly staging files, not published daily archives.
    """
    with Dataset(path, "r+") as data:
        name = "precip_timing_source_id"
        reference = data["precip_source_id"]
        if name in data.variables:
            variable = data[name]
            if variable.dimensions != reference.dimensions or variable.dtype != np.dtype("u1"):
                raise ValueError(f"Unexpected timing-source schema: {path}")
            return False
        chunks = reference.chunking()
        variable = data.createVariable(name, "u1", reference.dimensions, zlib=True,
            complevel=2, shuffle=True, chunksizes=None if chunks == "contiguous" else tuple(chunks))
        variable.long_name = "source supplying the within-block hourly timing pattern"
        variable.flag_values = np.array([0, *SOURCE_IDS.values()], np.uint8)
        variable.flag_meanings = "missing mrms_pass2 mrms_pass1 stage4_archive stage4_realtime nldas2 hrrr stage4_06h_constrained"
        variable.comment = "Zero means no separate within-block timing provenance (not reconciled or unavailable)."
        variable[:] = 0
        return True
