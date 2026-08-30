from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from hydro_ops.wrf_hydro.daily_output import (
    load_reducers,
    reduce_hourly_files,
    reduce_samples,
)


def test_load_reducers_assigns_every_known_ldasout_variable() -> None:
    path = Path(__file__).parents[1] / "config" / "wrf_hydro_daily_reducers.toml"
    reducers = load_reducers(path, "LDASOUT")

    assert reducers["SOIL_T"] == "mean"
    assert reducers["ISNOW"] == "last"
    assert reducers["ACCET"] == "omit"


def test_channel_interval_volume_is_summed() -> None:
    path = Path(__file__).parents[1] / "config" / "wrf_hydro_daily_reducers.toml"
    reducers = load_reducers(path, "CHRTOUT")

    assert reducers["streamflow"] == "mean"
    assert reducers["qBtmVertRunoff"] == "sum"


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("mean", [2.0, 3.0]),
        ("sum", [4.0, 6.0]),
        ("minimum", [1.0, 2.0]),
        ("maximum", [3.0, 4.0]),
        ("first", [1.0, 2.0]),
        ("last", [3.0, 4.0]),
    ],
)
def test_reduce_samples(method: str, expected: list[float]) -> None:
    values = np.array([[1.0, 2.0], [3.0, 4.0]])
    np.testing.assert_allclose(reduce_samples(values, method), expected)


def test_reduce_samples_does_not_hide_missing_hours() -> None:
    values = np.array([[1.0], [np.nan]])
    assert np.isnan(reduce_samples(values, "mean")[0])


def test_unknown_reducer_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown daily reducer"):
        reduce_samples(np.ones((2, 1)), "median")


def test_daily_oracle_records_bounds_and_variable_method(tmp_path: Path) -> None:
    paths = []
    for hour in range(24):
        path = tmp_path / f"hour-{hour}.nc"
        dataset = xr.Dataset(
            {"streamflow": (("feature_id",), [float(hour)])},
            coords={
                "time": [np.datetime64("2026-01-01T01") + np.timedelta64(hour, "h")],
                "feature_id": [7],
            },
        )
        dataset.to_netcdf(path)
        paths.append(path)

    result = reduce_hourly_files(paths, "CHRTOUT", {"streamflow": "mean"})

    assert result.attrs["temporal_resolution"] == "P1D"
    assert result.attrs["aggregation_sample_count"] == 24
    assert result["streamflow"].attrs["cell_methods"] == "time: mean"
    assert result["streamflow"].item() == pytest.approx(11.5)
    np.testing.assert_array_equal(
        result["time_bounds"].values,
        [[np.datetime64("2026-01-01T00"), np.datetime64("2026-01-02T00")]],
    )
