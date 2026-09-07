from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAILY_IO_PATCH = PROJECT_ROOT / "patches/wrf_hydro-5.4.0-daily-io.patch"
PATCH = PROJECT_ROOT / "patches/wrf_hydro-5.4.0-native-daily-output.patch"
COMPRESSION_PATCH = PROJECT_ROOT / "patches/wrf_hydro-5.4.0-netcdf-compression.patch"


def test_daily_input_patch_resolves_production_hierarchy() -> None:
    text = DAILY_IO_PATCH.read_text()
    hierarchy = 'out_date(1:4)//"/"//out_date(6:7)//"/"'
    assert text.count(hierarchy) == 2
    assert text.count('out_date(1:4)//out_date(6:7)//out_date(9:10)') >= 4


def test_native_daily_patch_uses_namelist_controls() -> None:
    text = PATCH.read_text()
    for name in (
        "CHRTOUT_HOURLY",
        "CHRTOUT_DAILY",
        "LDASOUT_HOURLY",
        "LDASOUT_DAILY",
    ):
        assert name in text
    assert "get_environment_variable" not in text


def test_native_daily_patch_preserves_upstream_defaults() -> None:
    text = PATCH.read_text()
    assert "+    CHRTOUT_HOURLY = 1" in text
    assert "+    CHRTOUT_DAILY = 0" in text
    assert "+    LDASOUT_HOURLY = 1" in text
    assert "+    LDASOUT_DAILY = 0" in text


def test_compression_patch_covers_runtime_writers_and_restarts() -> None:
    text = COMPRESSION_PATCH.read_text()
    for source in (
        "module_hrldas_netcdf_io.F",
        "module_HYDRO_io.F90",
        "module_NWM_io.F90",
        "module_nudging_io.F90",
    ):
        assert source in text
    assert "nf90_def_var_deflate(ncid, varid, 1, 1, 2)" in text
    assert '"io_form_outputs": 3' in text
    assert "io_form_outputs = 3" in text
