"""Topology selection and consistency checks for NWM domain subsets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hydro_ops.nwm_subset import GridWindow


@dataclass(frozen=True)
class SpatialSelection:
    polyids: np.ndarray
    overlaps: np.ndarray
    data: dict[str, np.ndarray]


def downstream_closure(seed_ids: np.ndarray, links: np.ndarray, to_links: np.ndarray) -> np.ndarray:
    """Keep seed reaches and their downstream paths while they remain in the candidate graph."""
    candidates = {int(link) for link in links}
    downstream = {int(link): int(to_link) for link, to_link in zip(links, to_links, strict=True)}
    retained: set[int] = set()
    for seed in seed_ids:
        current = int(seed)
        visited: set[int] = set()
        while current in candidates and current not in visited:
            retained.add(current)
            visited.add(current)
            current = downstream.get(current, 0)
    return np.asarray([link for link in links if int(link) in retained], dtype=links.dtype)


def localize_routing_indices(
    i_index: np.ndarray, j_index: np.ndarray, window: GridWindow
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Select 1-based global routing indices and shift them into a local 1-based window."""
    keep = (
        (i_index >= window.west_east_start + 1)
        & (i_index <= window.west_east_end + 1)
        & (j_index >= window.south_north_start + 1)
        & (j_index <= window.south_north_end + 1)
    )
    return (
        keep,
        i_index[keep] - window.west_east_start,
        j_index[keep] - window.south_north_start,
    )


def boundary_influence(
    full_links: np.ndarray,
    full_to_links: np.ndarray,
    retained_links: np.ndarray,
    retained_to_links: np.ndarray,
    incomplete_catchments: np.ndarray,
) -> np.ndarray:
    """Flag retained reaches affected by a clipped upstream link or catchment."""
    retained = set(map(int, retained_links))
    seeds = {int(value) for value in incomplete_catchments if int(value) in retained}
    for source, target in zip(full_links, full_to_links, strict=True):
        if int(target) in retained and int(source) not in retained:
            seeds.add(int(target))
    downstream = {
        int(source): int(target)
        for source, target in zip(retained_links, retained_to_links, strict=True)
    }
    affected: set[int] = set()
    for seed in seeds:
        current = seed
        visited: set[int] = set()
        while current in retained and current not in visited:
            affected.add(current)
            visited.add(current)
            current = downstream.get(current, 0)
    return np.asarray([int(link) in affected for link in retained_links], dtype=bool)


def validate_topology(
    links: np.ndarray,
    to_links: np.ndarray,
    ascending_index: np.ndarray,
    spatial: SpatialSelection,
    groundwater_comids: np.ndarray,
    groundwater_basins: np.ndarray,
    routing_shape: tuple[int, int],
) -> list[str]:
    errors: list[str] = []
    link_set = set(map(int, links))
    if len(link_set) != len(links):
        errors.append("RouteLink link IDs are not unique")
    outside = sorted({int(value) for value in to_links if value != 0 and int(value) not in link_set})
    if outside:
        errors.append(f"RouteLink has {len(outside)} unresolved downstream IDs")
    if sorted(map(int, ascending_index)) != list(range(len(links))):
        errors.append("ascendingIndex is not a zero-based permutation")
    if int(spatial.overlaps.sum()) != len(spatial.data["IDmask"]):
        errors.append("spatialweights overlaps do not sum to the data dimension")
    if not set(map(int, spatial.polyids)).issubset(link_set):
        errors.append("spatialweights contains polyids absent from RouteLink")
    expected = np.repeat(spatial.polyids, spatial.overlaps)
    if not np.array_equal(expected, spatial.data["IDmask"]):
        errors.append("spatialweights IDmask ordering is inconsistent with polyid/overlaps")
    ny, nx = routing_shape
    if len(spatial.data["i_index"]):
        if spatial.data["i_index"].min() < 1 or spatial.data["i_index"].max() > nx:
            errors.append("spatialweights i_index is outside the local routing grid")
        if spatial.data["j_index"].min() < 1 or spatial.data["j_index"].max() > ny:
            errors.append("spatialweights j_index is outside the local routing grid")
    if not np.array_equal(groundwater_comids, spatial.polyids):
        errors.append("GWBUCKPARM ComID order does not match spatialweights polyid")
    if not np.array_equal(groundwater_basins, np.arange(1, len(groundwater_basins) + 1)):
        errors.append("GWBUCKPARM Basin is not contiguous and one-based")
    return errors
