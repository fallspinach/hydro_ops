#!/usr/bin/env python3
"""Map missing forcing coverage and a distance-limited NWM run mask."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from netCDF4 import Dataset
from scipy.ndimage import distance_transform_edt

VARIABLES = ("T2D", "Q2D", "U2D", "V2D", "PSFC", "LWDOWN", "SWDOWN", "RAINRATE")


def block_max(values: np.ndarray, factor: int) -> np.ndarray:
    ny = values.shape[0] // factor * factor
    nx = values.shape[1] // factor * factor
    return values[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor).max((1, 3))


def block_mean(values: np.ndarray, factor: int) -> np.ndarray:
    ny = values.shape[0] // factor * factor
    nx = values.shape[1] // factor * factor
    return values[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor).mean((1, 3))


def block_nanmax(values: np.ndarray, factor: int) -> np.ndarray:
    ny = values.shape[0] // factor * factor
    nx = values.shape[1] // factor * factor
    reshaped = values[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor)
    finite = np.isfinite(reshaped)
    return np.where(finite.any((1, 3)), np.max(np.where(finite, reshaped, -np.inf), axis=(1, 3)), np.nan)


def mapped_land_cells(path: Path, shape: tuple[int, int], aggregation: int = 4) -> np.ndarray:
    mapped = np.zeros(shape, dtype=bool)
    with Dataset(path) as dataset:
        size = len(dataset.dimensions["data"])
        for start in range(0, size, 5_000_000):
            stop = min(start + 5_000_000, size)
            columns = (np.asarray(dataset["i_index"][start:stop], dtype=np.int64) - 1) // aggregation
            rows = (np.asarray(dataset["j_index"][start:stop], dtype=np.int64) - 1) // aggregation
            inside = (
                (rows >= 0)
                & (rows < shape[0])
                & (columns >= 0)
                & (columns < shape[1])
            )
            mapped[rows[inside], columns[inside]] = True
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forcing", type=Path)
    parser.add_argument("--wrfinput", required=True, type=Path)
    parser.add_argument("--spatialweights", required=True, type=Path)
    parser.add_argument("--time-index", type=int, default=1)
    parser.add_argument("--maximum-donor-km", type=float, default=25.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--downsample", type=int, default=4)
    args = parser.parse_args()

    with Dataset(args.wrfinput) as static:
        xland = np.asarray(static["XLAND"][0])
    land = xland == 1
    mapped = mapped_land_cells(args.spatialweights, land.shape)

    missing_count = np.zeros(land.shape, dtype=np.uint8)
    maximum_distance = np.zeros(land.shape, dtype=np.float32)
    variable_counts: dict[str, int] = {}
    with Dataset(args.forcing) as dataset:
        dataset.set_auto_mask(False)
        latitude = np.asarray(dataset["lat"][:], dtype=np.float32)
        longitude = np.asarray(dataset["lon"][:], dtype=np.float32)
        timestamp = str(dataset["time"][args.time_index])
        time_units = str(dataset["time"].units)
        for name in VARIABLES:
            variable = dataset[name]
            values = np.asarray(variable[args.time_index])
            fill = variable.getncattr("_FillValue")
            missing = ~np.isfinite(values) | (values == fill) | (values < -1.0e20)
            variable_counts[name] = int(missing.sum())
            missing_count += missing
            if missing.any():
                distance = distance_transform_edt(missing).astype(np.float32)
                maximum_distance[missing] = np.maximum(maximum_distance[missing], distance[missing])

    missing = missing_count > 0
    repairable = missing & land & (maximum_distance <= args.maximum_donor_km)
    deactivate = missing & land & ~repairable
    mapped_deactivate = deactivate & mapped
    category = np.zeros(land.shape, dtype=np.uint8)
    category[missing & ~land] = 1
    category[repairable] = 2
    category[deactivate] = 3
    category[mapped_deactivate] = 4

    factor = args.downsample
    lon_plot = block_mean(longitude, factor)
    lat_plot = block_mean(latitude, factor)
    count_plot = block_max(missing_count, factor)
    distance_plot = block_nanmax(np.where(missing & land, maximum_distance, np.nan), factor)
    category_plot = block_max(category, factor)

    figure, axes = plt.subplots(1, 3, figsize=(21, 7), constrained_layout=True)
    common = {"x": lon_plot, "y": lat_plot, "shading": "nearest", "rasterized": True}
    first = axes[0].pcolormesh(common["x"], common["y"], count_plot, shading="nearest", cmap="magma", vmin=0, vmax=len(VARIABLES), rasterized=True)
    figure.colorbar(first, ax=axes[0], label="Maximum missing-variable count in 4×4 block")
    axes[0].set_title("Missing forcing fields")

    second = axes[1].pcolormesh(common["x"], common["y"], distance_plot, shading="nearest", cmap="viridis", vmin=0, vmax=100, rasterized=True)
    figure.colorbar(second, ax=axes[1], label="Nearest valid donor distance (km; clipped at 100)")
    axes[1].set_title("Worst donor distance on original land")

    colors = ["#e8e8e8", "#8ecae6", "#ffb703", "#d62828", "#7b2cbf"]
    labels = [
        "No missing field",
        "Missing on original water",
        f"Land: repairable ≤{args.maximum_donor_km:g} km",
        f"Land: deactivate >{args.maximum_donor_km:g} km",
        "Deactivate; hydrologically mapped",
    ]
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(np.arange(-0.5, 5.5, 1), cmap.N)
    axes[2].pcolormesh(common["x"], common["y"], category_plot, shading="nearest", cmap=cmap, norm=norm, rasterized=True)
    axes[2].legend(handles=[Patch(color=color, label=label) for color, label in zip(colors, labels, strict=True)], loc="lower left", fontsize=8)
    axes[2].set_title("Proposed 25-km run-mask decision")

    for axis in axes:
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
        axis.set_xlim(-135, -60)
        axis.set_ylim(20, 55)
        axis.grid(alpha=0.2)

    figure.suptitle(
        f"NWM CONUS forcing gaps: {args.forcing.name}, record {args.time_index}\n"
        f"time={timestamp} ({time_units}); 1-km masks aggregated 4×4 only for display",
        fontsize=13,
    )
    text = (
        f"Original land with ≥1 missing field: {int((missing & land).sum()):,}\n"
        f"Repairable land ≤{args.maximum_donor_km:g} km: {int(repairable.sum()):,}\n"
        f"Proposed deactivated land: {int(deactivate.sum()):,}\n"
        f"Mapped cells proposed deactivated: {int(mapped_deactivate.sum()):,}"
    )
    figure.text(0.5, 0.015, text, ha="center", va="bottom", fontsize=10)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=180)
    plt.close(figure)
    print(args.output)
    print(text)
    print("missing_by_variable=" + ",".join(f"{key}:{value}" for key, value in variable_counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
