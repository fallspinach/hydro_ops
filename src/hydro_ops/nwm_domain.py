"""Structural compatibility checks for operational NWM/WRF-Hydro domain files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from netCDF4 import Dataset  # type: ignore[import-untyped]

DOMAIN_FILES = (
    "Diversion_CONUS.nc",
    "Fulldom_CONUS_FullRouting.nc",
    "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc",
    "GWBUCKPARM_CONUS_FullRouting.nc",
    "RouteLink_CONUS.nc",
    "geo_em_CONUS.nc",
    "hydro2dtbl_CONUS_FullRouting.nc",
    "nudgingParams_CONUS.nc",
    "reservoir_index_AnA.nc",
    "reservoir_index_Extended_AnA.nc",
    "reservoir_index_Medium_Range.nc",
    "reservoir_index_Short_Range.nc",
    "soilproperties_CONUS_FullRouting.nc",
    "spatialweights_CONUS_FullRouting.nc",
    "wrfinput_CONUS.nc",
)


@dataclass(frozen=True)
class Schema:
    dimensions: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    role: str = ""


# These are the minimum structural interfaces exercised by WRF-Hydro 5.4.0's
# NWM example/configuration, not a claim that every scientifically useful field
# has been validated.  Product-specific files without a distributed example are
# kept at a conservative minimum and reported as such.
SCHEMAS = {
    "Fulldom_CONUS_FullRouting.nc": Schema(
        ("x", "y"),
        ("CHANNELGRID", "FLOWDIRECTION", "TOPOGRAPHY", "LAKEGRID", "landuse"),
        "250-m terrain-routing grid",
    ),
    "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc": Schema(
        ("x", "y"), ("x", "y", "crs"), "1-km output geolocation metadata"
    ),
    "GWBUCKPARM_CONUS_FullRouting.nc": Schema(
        ("BasinDim",),
        ("Basin", "ComID", "Coeff", "Expon", "Zmax", "Zinit"),
        "groundwater bucket parameters",
    ),
    "RouteLink_CONUS.nc": Schema(
        ("feature_id",),
        ("link", "from", "to", "Length", "So", "MusK", "MusX", "ascendingIndex"),
        "channel hydrofabric and routing parameters",
    ),
    "geo_em_CONUS.nc": Schema(
        ("south_north", "west_east"),
        ("HGT_M", "LANDMASK", "LU_INDEX", "XLAT_M", "XLONG_M"),
        "Noah-MP/geogrid static domain",
    ),
    "hydro2dtbl_CONUS_FullRouting.nc": Schema(
        ("south_north", "west_east"),
        ("LKSAT", "NEXP", "OV_ROUGH2D", "SMCMAX1", "SMCREF1", "SMCWLT1"),
        "spatial hydrologic parameters",
    ),
    "nudgingParams_CONUS.nc": Schema(
        ("stationIdInd",),
        ("stationId", "G", "R", "tau"),
        "streamflow nudging parameters",
    ),
    "soilproperties_CONUS_FullRouting.nc": Schema(
        ("south_north", "west_east", "soil_layers_stag"),
        ("bexp", "dksat", "smcmax", "smcref", "smcwlt"),
        "spatial Noah-MP soil parameters",
    ),
    "spatialweights_CONUS_FullRouting.nc": Schema(
        ("data", "polyid"),
        ("i_index", "j_index", "polyid", "regridweight", "weight"),
        "land-grid to groundwater-basin mapping",
    ),
    "wrfinput_CONUS.nc": Schema(
        ("Time", "south_north", "west_east", "soil_layers_stag"),
        ("HGT", "ISLTYP", "IVGTYP", "SMOIS", "TSLB", "XLAT", "XLONG"),
        "Noah-MP initial/static land fields",
    ),
}


def inspect_file(path: Path) -> dict[str, Any]:
    """Inspect one domain file without reading its large data arrays."""
    record: dict[str, Any] = {"path": str(path), "bytes": None, "status": "pending"}
    if not path.is_file():
        return record
    record["bytes"] = path.stat().st_size
    try:
        with Dataset(path, "r") as dataset:
            dimensions = {name: len(value) for name, value in dataset.dimensions.items()}
            variables = sorted(dataset.variables)
            record.update(
                format=dataset.file_format,
                dimensions=dimensions,
                variables=variables,
            )
    except (OSError, RuntimeError) as exc:
        record.update(status="incompatible", errors=[f"NetCDF open failed: {exc}"])
        return record

    schema = SCHEMAS.get(path.name)
    errors: list[str] = []
    if schema:
        errors += [
            f"missing dimension: {name}" for name in schema.dimensions if name not in dimensions
        ]
        errors += [
            f"missing variable: {name}" for name in schema.variables if name not in variables
        ]
        record["role"] = schema.role
        record["schema_basis"] = "WRF-Hydro 5.4.0 NWM example/interface"
    else:
        record["schema_basis"] = (
            "NetCDF readability only; cross-file identifiers checked separately"
        )
    record["errors"] = errors
    record["status"] = "incompatible" if errors else "compatible"
    return record


def _same_grid(records: dict[str, dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    shapes = {}
    for name in names:
        dims = records[name].get("dimensions", {})
        if "south_north" in dims and "west_east" in dims:
            shapes[name] = [dims["south_north"], dims["west_east"]]
    return {
        "name": "one_km_land_grid_dimensions",
        "status": "pending"
        if len(shapes) != len(names)
        else ("compatible" if len({tuple(v) for v in shapes.values()}) == 1 else "incompatible"),
        "values": shapes,
    }


def inventory(root: Path) -> dict[str, Any]:
    """Inventory all public NWM 3.1 CONUS domain files and cross-file contracts."""
    domain = root / "domain"
    records = {name: inspect_file(domain / name) for name in DOMAIN_FILES}
    checks = [
        _same_grid(
            records,
            (
                "geo_em_CONUS.nc",
                "hydro2dtbl_CONUS_FullRouting.nc",
                "soilproperties_CONUS_FullRouting.nc",
                "wrfinput_CONUS.nc",
            ),
        )
    ]
    counts = {
        status: sum(r["status"] == status for r in records.values())
        for status in ("compatible", "incompatible", "pending")
    }
    blockers = []
    if not (domain / "LAKEPARM_CONUS.nc").is_file():
        blockers.append(
            "LAKEPARM_CONUS.nc is referenced by hydro.namelist but is not in the public NCO domain bundle"
        )
    blockers.append(
        "a timestamp-matched Noah-MP, hydro, and nudging restart set is not part of the public static bundle"
    )
    complete = counts["pending"] == 0
    compatible = (
        complete
        and counts["incompatible"] == 0
        and all(c["status"] == "compatible" for c in checks)
    )
    return {
        "created": datetime.now(UTC).isoformat(),
        "root": str(root),
        "scope": "structural compatibility with WRF-Hydro 5.4.0 and NWM 3.1 analysis-assimilation interfaces",
        "summary": {**counts, "complete": complete, "structurally_compatible": compatible},
        "files": records,
        "cross_file_checks": checks,
        "run_blockers_outside_domain_inventory": blockers,
    }


def write_inventory(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
