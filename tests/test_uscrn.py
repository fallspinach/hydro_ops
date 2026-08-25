from __future__ import annotations

from pathlib import Path

import numpy as np

from hydro_ops.observations.uscrn import read_hourly02


def test_read_hourly02_applies_units_flags_and_ending_time(tmp_path: Path) -> None:
    rows = [
        (
            "53150 20210101 0100 20201231 1700 2.622 -119.82 37.76 1.7 2.7 3.7 1.7 "
            "0.0 47 0 118 0 0 0 C -1.9 0 -0.7 0 -3.1 0 39 0 "
            "0.1 0.1 0.1 0.1 0.1 1.4 2.6 3.4 4.3 5.5"
        ),
        (
            "53150 20210101 0200 20201231 1800 2.622 -119.82 37.76 1.9 -9999 1.9 1.0 "
            "0.0 20 1 20 0 0 0 C -3.5 0 -3.0 0 -4.0 0 50 1 "
            "0.1 0.1 0.1 0.1 0.1 1.4 2.6 3.4 4.3 5.5"
        ),
    ]
    path = tmp_path / "station.txt"
    path.write_text("\n".join(rows) + "\n")
    observations = read_hourly02(path)
    assert observations.station_id == "53150"
    assert observations.time[0] == np.datetime64("2021-01-01T01:00")
    np.testing.assert_allclose(observations.temperature_k[0], 275.85)
    assert np.isnan(observations.temperature_k[1])
    assert np.isnan(observations.shortwave_w_m2[1])
    assert np.isnan(observations.relative_humidity[1])
