import numpy as np

from hydro_ops.nwm_subset import GridWindow
from hydro_ops.nwm_topology import (
    SpatialSelection,
    boundary_influence,
    downstream_closure,
    localize_routing_indices,
    validate_topology,
)


def test_downstream_closure_stops_at_subset_boundary() -> None:
    links = np.array([10, 20, 30, 40, 50])
    to_links = np.array([30, 30, 40, 99, 0])
    assert np.array_equal(downstream_closure(np.array([10, 20]), links, to_links), [10, 20, 30, 40])


def test_localize_routing_indices() -> None:
    window = GridWindow(10, 19, 20, 29)
    keep, local_i, local_j = localize_routing_indices(
        np.array([10, 11, 20, 21]), np.array([21, 21, 30, 30]), window
    )
    assert np.array_equal(keep, [False, True, True, False])
    assert np.array_equal(local_i, [1, 10])
    assert np.array_equal(local_j, [1, 10])


def test_boundary_influence_propagates_downstream() -> None:
    full_links = np.array([5, 10, 20, 30, 40, 50])
    full_to = np.array([20, 20, 30, 40, 0, 0])
    retained_links = np.array([10, 20, 30, 40, 50])
    retained_to = np.array([20, 30, 40, 0, 0])

    affected = boundary_influence(
        full_links,
        full_to,
        retained_links,
        retained_to,
        incomplete_catchments=np.array([50]),
    )

    assert np.array_equal(affected, [False, True, True, True, True])


def test_validate_consistent_topology() -> None:
    spatial = SpatialSelection(
        polyids=np.array([10, 20]),
        overlaps=np.array([2, 1]),
        data={
            "IDmask": np.array([10, 10, 20]),
            "i_index": np.array([1, 2, 3]),
            "j_index": np.array([2, 2, 3]),
        },
    )
    errors = validate_topology(
        links=np.array([20, 10]),
        to_links=np.array([0, 20]),
        ascending_index=np.array([1, 0]),
        spatial=spatial,
        groundwater_comids=np.array([10, 20]),
        groundwater_basins=np.array([1, 2]),
        routing_shape=(4, 4),
    )
    assert errors == []
