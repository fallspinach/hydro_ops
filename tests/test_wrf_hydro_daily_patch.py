from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATCH = PROJECT_ROOT / "patches/wrf_hydro-5.4.0-native-daily-output.patch"


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
