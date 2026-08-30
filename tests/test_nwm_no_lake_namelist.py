from pathlib import Path

from hydro_ops.nwm_config import create_no_lake_namelist


def test_create_no_lake_namelist_preserves_source_and_disables_lakes(tmp_path: Path) -> None:
    source = tmp_path / "hydro.namelist"
    original = """&hydro_nlist
lake_option = 3
route_lake_f = './DOMAIN/LAKEPARM_CONUS.nc'
/
&reservoir_nlist
reservoir_persistence_usgs = .true.
reservoir_persistence_usace = .true.
reservoir_rfc_forecasts = .true.
/
"""
    source.write_text(original)
    destination = tmp_path / "no_lakes" / "hydro.namelist"
    create_no_lake_namelist(source, destination)
    result = destination.read_text()
    assert source.read_text() == original
    assert "lake_option = 0" in result
    assert "route_lake_f = ''" in result
    assert result.count("= .false.") == 3
