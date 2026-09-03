from datetime import date
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.forcing.model_interval import (
    expected_endpoint_times,
    reduce_model_interval_forcing,
)


def write_chunk(path: Path, start: str, values: np.ndarray) -> None:
    times = np.arange(
        np.datetime64(start), np.datetime64(start) + np.timedelta64(24, "h"), np.timedelta64(1, "h")
    )
    dataset = xr.Dataset(
        {
            "T2D": (("time", "y", "x"), values[:, None, None]),
            "RAINRATE": (("time", "y", "x"), np.ones((24, 1, 1), dtype=np.float32)),
        },
        coords={"time": times, "y": [0], "x": [0]},
    )
    dataset["T2D"].attrs["units"] = "K"
    dataset["RAINRATE"].attrs["units"] = "kg m-2 s-1"
    dataset.to_netcdf(path)


def test_expected_model_interval_endpoints() -> None:
    values = expected_endpoint_times(date(2026, 9, 1))
    assert values[0] == np.datetime64("2026-09-01T01:00:00")
    assert values[-1] == np.datetime64("2026-09-02T00:00:00")
    assert len(values) == 24


def test_reduce_across_calendar_chunks(tmp_path: Path) -> None:
    first = tmp_path / "20260901.LDASIN_DOMAIN1"
    second = tmp_path / "20260902.LDASIN_DOMAIN1"
    write_chunk(first, "2026-09-01T00", np.arange(24, dtype=np.float32))
    write_chunk(second, "2026-09-02T00", np.arange(24, 48, dtype=np.float32))

    result = reduce_model_interval_forcing(
        [first, second],
        date(2026, 9, 1),
        {"T2D": "mean", "RAINRATE": "integral"},
        output_names={"RAINRATE": "RAIN_DEPTH"},
        output_units={"RAINRATE": "kg m-2"},
    ).compute()
    try:
        # Endpoint samples are 1..23 from the first chunk and 24 from the second.
        np.testing.assert_allclose(result["T2D"].values, [[[12.5]]])
        np.testing.assert_allclose(result["RAIN_DEPTH"].values, [[[86400.0]]])
        assert result["RAIN_DEPTH"].attrs["units"] == "kg m-2"
        assert result["RAIN_DEPTH"].attrs["standard_name"] == "precipitation_amount"
        assert result.attrs["day_definition"] == "model_interval"
        assert result.attrs["aggregation_sample_count"] == 24
        assert result.time.values[0] == np.datetime64("2026-09-01T12:00:00")
        np.testing.assert_array_equal(
            result.time_bounds.values,
            [[np.datetime64("2026-09-01T00"), np.datetime64("2026-09-02T00")]],
        )
    finally:
        result.close()


def test_missing_boundary_hour_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first.nc"
    write_chunk(first, "2026-09-01T00", np.arange(24, dtype=np.float32))
    try:
        reduce_model_interval_forcing(
            [first], date(2026, 9, 1), {"T2D": "mean"}
        )
    except ValueError as error:
        assert "2026-09-02T00" in str(error)
    else:
        raise AssertionError("missing next-day 00 UTC endpoint was accepted")
