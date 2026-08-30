from pathlib import Path

import pytest

from hydro_ops.forcing.streams import (
    baseline_root,
    forcing_stream_root,
    validate_stream_output_root,
)


def test_canonical_stream_roots_are_siblings(tmp_path: Path) -> None:
    assert forcing_stream_root(tmp_path, "nrt") == tmp_path / "outputs/forcing/nwm/nrt"
    assert forcing_stream_root(tmp_path, "retro") == tmp_path / "outputs/forcing/nwm/retro"


def test_roots_fall_back_until_layout_is_migrated(tmp_path: Path) -> None:
    legacy_baseline = tmp_path / "outputs/forcing/nwm"
    legacy_nrt = tmp_path / "outputs/forcing/nwm_prism/nrt"
    legacy_baseline.mkdir(parents=True)
    legacy_nrt.mkdir(parents=True)
    assert baseline_root(tmp_path) == legacy_baseline
    assert forcing_stream_root(tmp_path, "nrt") == legacy_nrt

    canonical_baseline = legacy_baseline / "baseline"
    canonical_nrt = legacy_baseline / "nrt"
    canonical_baseline.mkdir()
    canonical_nrt.mkdir()
    assert baseline_root(tmp_path) == canonical_baseline
    assert forcing_stream_root(tmp_path, "nrt") == canonical_nrt


def test_stream_root_must_match_selected_stream(tmp_path: Path) -> None:
    assert validate_stream_output_root(tmp_path / "product/nrt", "nrt").name == "nrt"
    with pytest.raises(ValueError, match="must end"):
        validate_stream_output_root(tmp_path / "product/retro", "nrt")
    with pytest.raises(ValueError, match="cannot be published"):
        validate_stream_output_root(tmp_path / "retro/archive/nrt", "nrt")
