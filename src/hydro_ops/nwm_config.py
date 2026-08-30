"""Create project-specific WRF-Hydro/NWM run configurations."""

from __future__ import annotations

import re
from pathlib import Path

NO_LAKE_OVERRIDES = {
    "lake_option": "0",
    "route_lake_f": "''",
    "reservoir_persistence_usgs": ".false.",
    "reservoir_persistence_usace": ".false.",
    "reservoir_rfc_forecasts": ".false.",
}


def create_no_lake_namelist(source: Path, destination: Path) -> Path:
    """Copy a hydro namelist while disabling all lake/reservoir behavior."""
    text = source.read_text()
    for name, value in NO_LAKE_OVERRIDES.items():
        pattern = re.compile(rf"(?m)^(\s*{re.escape(name)}\s*=\s*)[^!\n]*(.*)$")
        text, count = pattern.subn(rf"\g<1>{value} \g<2>", text)
        if count != 1:
            raise ValueError(f"Expected exactly one {name} assignment; found {count}")
    marker = (
        "! hydro_ops derived no-lake configuration; source preserved at "
        f"{source}\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.write_text(marker + text)
    partial.replace(destination)
    return destination
