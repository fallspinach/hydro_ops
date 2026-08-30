#!/usr/bin/env python3
"""Create the gridded portion of an NWM CONUS subset and a clipping manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import netCDF4
import numpy as np

from hydro_ops.nwm_subset import GridWindow, window_from_bbox
from hydro_ops.nwm_topology import (
    SpatialSelection,
    boundary_influence,
    downstream_closure,
    localize_routing_indices,
    validate_topology,
)

ONE_KM_FILES = {
    "wrfinput_CONUS.nc": ("west_east", "south_north"),
    "soilproperties_CONUS_FullRouting.nc": ("west_east", "south_north"),
    "hydro2dtbl_CONUS_FullRouting.nc": ("west_east", "south_north"),
}
ROUTING_FILES = {
    "Fulldom_CONUS_FullRouting.nc": ("x", "y"),
}
SPATIAL_VARIABLES = ("IDmask", "weight", "regridweight", "i_index", "j_index")


def read_latlon(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with netCDF4.Dataset(path) as dataset:
        latitude = np.asarray(dataset.variables["XLAT"][0])
        longitude = np.asarray(dataset.variables["XLONG"][0])
    return latitude, longitude


def ncks_subset(source: Path, destination: Path, window: GridWindow, dims: tuple[str, str]) -> None:
    xdim, ydim = dims
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.partial")
    subprocess.run(
        [
            "ncks",
            "-O",
            "-4",
            "-L",
            "2",
            "-d",
            f"{xdim},{window.west_east_start},{window.west_east_end}",
            "-d",
            f"{ydim},{window.south_north_start},{window.south_north_end}",
            str(source),
            str(temporary),
        ],
        check=True,
    )
    temporary.replace(destination)


def subset_geo_em(source: Path, destination: Path, window: GridWindow) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.partial")
    subprocess.run(
        [
            "ncks", "-O", "-4", "-L", "2",
            "-d", f"west_east,{window.west_east_start},{window.west_east_end}",
            "-d", f"south_north,{window.south_north_start},{window.south_north_end}",
            "-d", f"west_east_stag,{window.west_east_start},{window.west_east_end + 1}",
            "-d", f"south_north_stag,{window.south_north_start},{window.south_north_end + 1}",
            str(source), str(temporary),
        ],
        check=True,
    )
    temporary.replace(destination)


def update_wrf_grid_metadata(path: Path, latitude: np.ndarray, longitude: np.ndarray) -> None:
    ny, nx = latitude.shape
    corner_lats = np.asarray(
        [latitude[0, 0], latitude[-1, 0], latitude[-1, -1], latitude[0, -1]] * 4,
        dtype=np.float32,
    )
    corner_lons = np.asarray(
        [longitude[0, 0], longitude[-1, 0], longitude[-1, -1], longitude[0, -1]] * 4,
        dtype=np.float32,
    )
    updates = {
        "WEST-EAST_GRID_DIMENSION": nx + 1,
        "SOUTH-NORTH_GRID_DIMENSION": ny + 1,
        "WEST-EAST_PATCH_START_UNSTAG": 1,
        "WEST-EAST_PATCH_END_UNSTAG": nx,
        "WEST-EAST_PATCH_START_STAG": 1,
        "WEST-EAST_PATCH_END_STAG": nx + 1,
        "SOUTH-NORTH_PATCH_START_UNSTAG": 1,
        "SOUTH-NORTH_PATCH_END_UNSTAG": ny,
        "SOUTH-NORTH_PATCH_START_STAG": 1,
        "SOUTH-NORTH_PATCH_END_STAG": ny + 1,
        "i_parent_start": 1,
        "j_parent_start": 1,
        "i_parent_end": nx + 1,
        "j_parent_end": ny + 1,
        "CEN_LAT": np.float32(np.nanmean(latitude)),
        "CEN_LON": np.float32(np.nanmean(longitude)),
        "corner_lats": corner_lats,
        "corner_lons": corner_lons,
    }
    with netCDF4.Dataset(path, "r+") as dataset:
        existing = set(dataset.ncattrs())
        for name, value in updates.items():
            if name in existing:
                dataset.setncattr(name, value)


def update_routing_geotransform(path: Path) -> None:
    with netCDF4.Dataset(path, "r+") as dataset:
        x = np.asarray(dataset.variables["x"][:])
        y = np.asarray(dataset.variables["y"][:])
        dx = float(abs(x[1] - x[0]))
        dy = float(abs(y[1] - y[0]))
        x_origin = float(x[0] - dx / 2)
        y_step = -dy if y[-1] < y[0] else dy
        y_origin = float(y[0] - y_step / 2)
        transform = f"{x_origin:.8f} {dx:g} 0 {y_origin:.8f} 0 {y_step:g} "
        if "GeoTransform" in dataset.ncattrs():
            dataset.setncattr("GeoTransform", transform)
        for variable in dataset.variables.values():
            if "GeoTransform" in variable.ncattrs():
                variable.setncattr("GeoTransform", transform)


def create_variable_like(destination: netCDF4.Dataset, source: netCDF4.Variable) -> netCDF4.Variable:
    fill_value = source.getncattr("_FillValue") if "_FillValue" in source.ncattrs() else None
    kwargs = {"fill_value": fill_value} if fill_value is not None else {}
    if source.dimensions and source.dtype.kind not in {"S", "U", "O"}:
        kwargs.update(zlib=True, complevel=2, shuffle=True)
    variable = destination.createVariable(source.name, source.datatype, source.dimensions, **kwargs)
    variable.setncatts({name: source.getncattr(name) for name in source.ncattrs() if name != "_FillValue"})
    return variable


def copy_indexed_file(
    source_path: Path,
    destination_path: Path,
    indexed_dimension: str,
    indices: np.ndarray,
    replacements: dict[str, np.ndarray] | None = None,
) -> None:
    replacements = replacements or {}
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.partial")
    with netCDF4.Dataset(source_path) as source, netCDF4.Dataset(temporary, "w", format="NETCDF4") as destination:
        for name, dimension in source.dimensions.items():
            destination.createDimension(name, len(indices) if name == indexed_dimension else len(dimension))
        destination.setncatts({name: source.getncattr(name) for name in source.ncattrs()})
        for name, source_variable in source.variables.items():
            output = create_variable_like(destination, source_variable)
            if name in replacements:
                output[:] = replacements[name]
            elif indexed_dimension in source_variable.dimensions:
                axis = source_variable.dimensions.index(indexed_dimension)
                selector = [slice(None)] * source_variable.ndim
                selector[axis] = indices
                output[:] = source_variable[tuple(selector)]
            else:
                output[:] = source_variable[:]
    temporary.replace(destination_path)


def select_route_candidates(route_path: Path, bbox: tuple[float, float, float, float]) -> dict[str, np.ndarray]:
    west, south, east, north = bbox
    with netCDF4.Dataset(route_path) as dataset:
        latitude = np.asarray(dataset.variables["lat"][:])
        longitude = np.asarray(dataset.variables["lon"][:])
        keep = (
            np.isfinite(latitude)
            & np.isfinite(longitude)
            & (longitude >= west)
            & (longitude <= east)
            & (latitude >= south)
            & (latitude <= north)
        )
        return {
            "indices": np.flatnonzero(keep),
            "link": np.asarray(dataset.variables["link"][:])[keep],
            "to": np.asarray(dataset.variables["to"][:])[keep],
        }


def select_spatialweights(
    path: Path, candidate_ids: np.ndarray, routing_window: GridWindow, chunk_size: int
) -> SpatialSelection:
    selected = set(map(int, candidate_ids))
    pieces: dict[str, list[np.ndarray]] = {name: [] for name in SPATIAL_VARIABLES}
    counts: dict[int, int] = {}
    with netCDF4.Dataset(path) as dataset:
        size = len(dataset.dimensions["data"])
        for start in range(0, size, chunk_size):
            stop = min(size, start + chunk_size)
            ids = np.asarray(dataset.variables["IDmask"][start:stop])
            candidate = np.isin(ids, candidate_ids)
            if not candidate.any():
                continue
            i_index = np.asarray(dataset.variables["i_index"][start:stop])
            j_index = np.asarray(dataset.variables["j_index"][start:stop])
            local_keep, local_i, local_j = localize_routing_indices(
                i_index[candidate], j_index[candidate], routing_window
            )
            positions = np.flatnonzero(candidate)[local_keep]
            if not len(positions):
                continue
            local_ids = ids[positions]
            pieces["IDmask"].append(local_ids)
            pieces["i_index"].append(local_i)
            pieces["j_index"].append(local_j)
            pieces["weight"].append(np.asarray(dataset.variables["weight"][start:stop])[positions])
            pieces["regridweight"].append(
                np.asarray(dataset.variables["regridweight"][start:stop])[positions]
            )
            unique, frequency = np.unique(local_ids, return_counts=True)
            for polyid, count in zip(unique, frequency, strict=True):
                counts[int(polyid)] = counts.get(int(polyid), 0) + int(count)
        source_polyids = np.asarray(dataset.variables["polyid"][:])
    polyids = np.asarray([value for value in source_polyids if int(value) in selected and int(value) in counts])
    overlaps = np.asarray([counts[int(value)] for value in polyids], dtype=np.int32)
    data = {
        name: np.concatenate(values) if values else np.array([], dtype=np.int32)
        for name, values in pieces.items()
    }
    return SpatialSelection(polyids=polyids, overlaps=overlaps, data=data)


def write_spatialweights(source_path: Path, destination_path: Path, selection: SpatialSelection) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.partial")
    with netCDF4.Dataset(source_path) as source, netCDF4.Dataset(temporary, "w", format="NETCDF4") as destination:
        destination.createDimension("polyid", len(selection.polyids))
        destination.createDimension("data", len(selection.data["IDmask"]))
        destination.setncatts({name: source.getncattr(name) for name in source.ncattrs()})
        for name, source_variable in source.variables.items():
            output = create_variable_like(destination, source_variable)
            if name == "polyid":
                output[:] = selection.polyids
            elif name == "overlaps":
                output[:] = selection.overlaps
            else:
                output[:] = selection.data[name]
    temporary.replace(destination_path)


def append_completeness_masks(
    path: Path, dimension: str, boundary_affected: np.ndarray
) -> None:
    with netCDF4.Dataset(path, "r+") as dataset:
        boundary = dataset.createVariable(
            "boundary_affected", "u1", (dimension,), zlib=True, complevel=2
        )
        boundary.long_name = "Flow is influenced by a clipped upstream reach or catchment"
        boundary.flag_values = np.asarray([0, 1], dtype=np.uint8)
        boundary.flag_meanings = "unaffected affected"
        boundary[:] = boundary_affected.astype(np.uint8)
        complete = dataset.createVariable(
            "complete_watershed", "u1", (dimension,), zlib=True, complevel=2
        )
        complete.long_name = "Entire modeled upstream drainage area is inside the subset"
        complete.flag_values = np.asarray([0, 1], dtype=np.uint8)
        complete.flag_meanings = "incomplete complete"
        complete[:] = (~boundary_affected).astype(np.uint8)


def write_routing_validity_grid(
    routing_path: Path,
    destination_path: Path,
    spatial: SpatialSelection,
    affected_polyids: np.ndarray,
) -> tuple[int, int]:
    with netCDF4.Dataset(routing_path) as routing:
        ny = len(routing.dimensions["y"])
        nx = len(routing.dimensions["x"])
        x_values = np.asarray(routing.variables["x"][:])
        y_values = np.asarray(routing.variables["y"][:])
        x_source = routing.variables["x"]
        y_source = routing.variables["y"]
        crs_source = routing.variables.get("crs")
        x_metadata = (
            x_source.datatype,
            {attr: x_source.getncattr(attr) for attr in x_source.ncattrs()},
        )
        y_metadata = (
            y_source.datatype,
            {attr: y_source.getncattr(attr) for attr in y_source.ncattrs()},
        )
        crs_metadata = (
            (
                crs_source.datatype,
                crs_source.dimensions,
                {attr: crs_source.getncattr(attr) for attr in crs_source.ncattrs()},
                np.asarray(crs_source[:]),
            )
            if crs_source is not None
            else None
        )
        crs_dimensions = (
            {name: len(routing.dimensions[name]) for name in crs_source.dimensions}
            if crs_source is not None
            else {}
        )
        global_attributes = {name: routing.getncattr(name) for name in routing.ncattrs()}
    boundary_grid = np.zeros((ny, nx), dtype=bool)
    complete_presence = np.zeros((ny, nx), dtype=bool)
    affected_record = np.isin(spatial.data["IDmask"], affected_polyids)
    rows = spatial.data["j_index"].astype(np.int64) - 1
    columns = spatial.data["i_index"].astype(np.int64) - 1
    np.logical_or.at(boundary_grid, (rows[affected_record], columns[affected_record]), True)
    np.logical_or.at(complete_presence, (rows[~affected_record], columns[~affected_record]), True)
    complete_grid = complete_presence & ~boundary_grid

    temporary = destination_path.with_name(f".{destination_path.name}.partial")
    with netCDF4.Dataset(temporary, "w", format="NETCDF4") as destination:
        destination.createDimension("y", ny)
        destination.createDimension("x", nx)
        for name, size in crs_dimensions.items():
            if name not in destination.dimensions:
                destination.createDimension(name, size)
        destination.setncatts(global_attributes)
        for name, values, metadata in (
            ("x", x_values, x_metadata),
            ("y", y_values, y_metadata),
        ):
            datatype, attributes = metadata
            variable = destination.createVariable(name, datatype, (name,))
            variable.setncatts(attributes)
            variable[:] = values
        if crs_metadata is not None:
            datatype, dimensions, attributes, values = crs_metadata
            crs = destination.createVariable("crs", datatype, dimensions)
            crs.setncatts(attributes)
            crs[:] = values
        for name, values, long_name in (
            (
                "boundary_affected",
                boundary_grid,
                "Routing cells associated with one or more boundary-affected catchments",
            ),
            (
                "complete_watershed",
                complete_grid,
                "Routing cells associated exclusively with spatially complete catchments",
            ),
        ):
            variable = destination.createVariable(name, "u1", ("y", "x"), zlib=True, complevel=2)
            variable.long_name = long_name
            variable.grid_mapping = "crs"
            variable.flag_values = np.asarray([0, 1], dtype=np.uint8)
            variable.flag_meanings = "false true"
            variable[:] = values.astype(np.uint8)
    temporary.replace(destination_path)
    return int(complete_grid.sum()), int(boundary_grid.sum())


def subset_network(
    domain_dir: Path,
    output_dir: Path,
    bbox: tuple[float, float, float, float],
    routing_window: GridWindow,
    chunk_size: int,
) -> dict[str, object]:
    route_source = domain_dir / "RouteLink_CONUS.nc"
    candidates = select_route_candidates(route_source, bbox)
    spatial = select_spatialweights(
        domain_dir / "spatialweights_CONUS_FullRouting.nc",
        candidates["link"],
        routing_window,
        chunk_size,
    )
    retained_ids = downstream_closure(spatial.polyids, candidates["link"], candidates["to"])
    retained_set = set(map(int, retained_ids))
    route_keep = np.asarray([int(value) in retained_set for value in candidates["link"]])
    route_indices = candidates["indices"][route_keep]
    route_links = candidates["link"][route_keep]
    route_to = candidates["to"][route_keep]
    route_to = np.asarray([value if int(value) in retained_set else 0 for value in route_to], dtype=route_to.dtype)
    ascending = np.argsort(route_links).astype(np.int32)
    replacements = {"to": route_to, "ascendingIndex": ascending}
    with netCDF4.Dataset(route_source) as source:
        if "from" in source.variables:
            route_from = np.asarray(source.variables["from"][:])[route_indices]
            replacements["from"] = np.asarray(
                [value if int(value) in retained_set else 0 for value in route_from], dtype=route_from.dtype
            )
        waterbody = np.zeros(len(route_indices), dtype=source.variables["NHDWaterbodyComID"].dtype)
        replacements["NHDWaterbodyComID"] = waterbody
        gages = netCDF4.chartostring(source.variables["gages"][route_indices])
    copy_indexed_file(
        route_source,
        output_dir / "RouteLink.nc",
        "feature_id",
        route_indices,
        replacements,
    )

    spatial_keep = np.asarray([int(value) in retained_set for value in spatial.polyids])
    kept_polyids = spatial.polyids[spatial_keep]
    keep_data = np.isin(spatial.data["IDmask"], kept_polyids)
    spatial = SpatialSelection(
        polyids=kept_polyids,
        overlaps=spatial.overlaps[spatial_keep],
        data={name: values[keep_data] for name, values in spatial.data.items()},
    )
    write_spatialweights(
        domain_dir / "spatialweights_CONUS_FullRouting.nc",
        output_dir / "spatialweights.nc",
        spatial,
    )

    spatial_source = domain_dir / "spatialweights_CONUS_FullRouting.nc"
    with netCDF4.Dataset(spatial_source) as source:
        full_polyids = np.asarray(source.variables["polyid"][:])
        full_overlaps = np.asarray(source.variables["overlaps"][:])
    overlap_for_polyid = {
        int(polyid): int(overlap)
        for polyid, overlap in zip(full_polyids, full_overlaps, strict=True)
    }
    incomplete_catchments = np.asarray(
        [
            polyid
            for polyid, overlap in zip(spatial.polyids, spatial.overlaps, strict=True)
            if int(overlap) < overlap_for_polyid[int(polyid)]
        ],
        dtype=spatial.polyids.dtype,
    )
    with netCDF4.Dataset(route_source) as source:
        full_links = np.asarray(source.variables["link"][:])
        full_to = np.asarray(source.variables["to"][:])
    route_boundary = boundary_influence(
        full_links,
        full_to,
        route_links,
        route_to,
        incomplete_catchments,
    )
    append_completeness_masks(output_dir / "RouteLink.nc", "feature_id", route_boundary)
    affected_links = route_links[route_boundary]
    polyid_boundary = np.isin(spatial.polyids, affected_links)
    append_completeness_masks(output_dir / "spatialweights.nc", "polyid", polyid_boundary)
    complete_grid_cells, boundary_grid_cells = write_routing_validity_grid(
        output_dir / "Fulldom_CONUS_FullRouting.nc",
        output_dir / "subset_flow_validity.nc",
        spatial,
        spatial.polyids[polyid_boundary],
    )

    gw_source = domain_dir / "GWBUCKPARM_CONUS_FullRouting.nc"
    with netCDF4.Dataset(gw_source) as source:
        comids = np.asarray(source.variables["ComID"][:])
        row_for_comid = {int(value): index for index, value in enumerate(comids)}
    missing_gw = [int(value) for value in spatial.polyids if int(value) not in row_for_comid]
    if missing_gw:
        raise RuntimeError(f"Missing {len(missing_gw)} selected COMIDs from GWBUCKPARM")
    gw_indices = np.asarray([row_for_comid[int(value)] for value in spatial.polyids])
    groundwater_basins = np.arange(1, len(gw_indices) + 1, dtype=np.int32)
    copy_indexed_file(
        gw_source,
        output_dir / "GWBUCKPARM.nc",
        "BasinDim",
        gw_indices,
        {"Basin": groundwater_basins},
    )

    selected_gages = {str(value).strip() for value in gages if str(value).strip()}
    nudging_source = domain_dir / "nudgingParams_CONUS.nc"
    with netCDF4.Dataset(nudging_source) as source:
        stations = netCDF4.chartostring(source.variables["stationId"][:])
        station_indices = np.asarray(
            [index for index, value in enumerate(stations) if str(value).strip() in selected_gages]
        )
    copy_indexed_file(
        nudging_source,
        output_dir / "nudgingParams.nc",
        "stationIdInd",
        station_indices,
    )

    with netCDF4.Dataset(output_dir / "RouteLink.nc") as route_output:
        written_links = np.asarray(route_output.variables["link"][:])
        written_to = np.asarray(route_output.variables["to"][:])
        written_ascending = np.asarray(route_output.variables["ascendingIndex"][:])
    with netCDF4.Dataset(output_dir / "spatialweights.nc") as spatial_output:
        written_spatial = SpatialSelection(
            polyids=np.asarray(spatial_output.variables["polyid"][:]),
            overlaps=np.asarray(spatial_output.variables["overlaps"][:]),
            data={name: np.asarray(spatial_output.variables[name][:]) for name in SPATIAL_VARIABLES},
        )
    with netCDF4.Dataset(output_dir / "GWBUCKPARM.nc") as groundwater_output:
        written_comids = np.asarray(groundwater_output.variables["ComID"][:])
        written_basins = np.asarray(groundwater_output.variables["Basin"][:])
    errors = validate_topology(
        written_links,
        written_to,
        written_ascending,
        written_spatial,
        written_comids,
        written_basins,
        routing_window.shape,
    )
    if errors:
        raise RuntimeError("Topology validation failed: " + "; ".join(errors))
    return {
        "route_links": len(route_links),
        "runoff_catchments": len(spatial.polyids),
        "spatial_weight_records": len(spatial.data["IDmask"]),
        "nudging_stations": len(station_indices),
        "complete_flow_reaches": int((~route_boundary).sum()),
        "boundary_affected_reaches": int(route_boundary.sum()),
        "partially_clipped_catchments": len(incomplete_catchments),
        "complete_routing_grid_cells": complete_grid_cells,
        "boundary_affected_routing_grid_cells": boundary_grid_cells,
        "boundary_policy": "truncate_zero_inflow_and_terminal_outlet",
        "lakes": "disabled",
        "diversions": "disabled",
        "topology_validation": "passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"), required=True)
    parser.add_argument("--padding", type=int, default=2, help="1-km grid-cell padding")
    parser.add_argument("--routing-factor", type=int, default=4)
    parser.add_argument("--spatial-chunk-size", type=int, default=2_000_000)
    parser.add_argument("--execute", action="store_true", help="Write gridded subset files")
    parser.add_argument("--gridded-only", action="store_true", help="Skip topology-aware network files")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    latitude, longitude = read_latlon(args.domain_dir / "wrfinput_CONUS.nc")
    one_km = window_from_bbox(latitude, longitude, tuple(args.bbox), args.padding)
    routing = one_km.refined(args.routing_factor)
    manifest = {
        "bbox": args.bbox,
        "padding_1km_cells": args.padding,
        "one_km_window": one_km.__dict__,
        "one_km_shape": one_km.shape,
        "routing_factor": args.routing_factor,
        "routing_window": routing.__dict__,
        "routing_shape": routing.shape,
        "gridded_files": sorted(
            [
                *ONE_KM_FILES,
                *ROUTING_FILES,
                "geo_em_CONUS.nc",
                "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc",
            ]
        ),
        "network_files_status": "planned" if not args.gridded_only else "not_clipped",
    }
    if not args.execute:
        print(json.dumps(manifest, indent=2))
        return
    for name, dims in ONE_KM_FILES.items():
        ncks_subset(args.domain_dir / name, args.output_dir / name, one_km, dims)
        update_wrf_grid_metadata(
            args.output_dir / name,
            latitude[one_km.south_north_start : one_km.south_north_end + 1,
                     one_km.west_east_start : one_km.west_east_end + 1],
            longitude[one_km.south_north_start : one_km.south_north_end + 1,
                      one_km.west_east_start : one_km.west_east_end + 1],
        )
    for name, dims in ROUTING_FILES.items():
        ncks_subset(args.domain_dir / name, args.output_dir / name, routing, dims)
        update_routing_geotransform(args.output_dir / name)
    subset_geo_em(
        args.domain_dir / "geo_em_CONUS.nc",
        args.output_dir / "geo_em_CONUS.nc",
        one_km,
    )
    update_wrf_grid_metadata(
        args.output_dir / "geo_em_CONUS.nc",
        latitude[one_km.south_north_start : one_km.south_north_end + 1,
                 one_km.west_east_start : one_km.west_east_end + 1],
        longitude[one_km.south_north_start : one_km.south_north_end + 1,
                  one_km.west_east_start : one_km.west_east_end + 1],
    )
    ncks_subset(
        args.domain_dir / "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc",
        args.output_dir / "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc",
        one_km,
        ("x", "y"),
    )
    update_routing_geotransform(args.output_dir / "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc")
    if not args.gridded_only:
        manifest["network"] = subset_network(
            args.domain_dir,
            args.output_dir,
            tuple(args.bbox),
            routing,
            args.spatial_chunk_size,
        )
        manifest["network_files_status"] = "validated"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "subset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
