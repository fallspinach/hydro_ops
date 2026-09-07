from pathlib import Path

import pytest

from hydro_ops.forcing.streams import (
    baseline_root,
    forcing_stream_root,
    validate_stream_output_root,
)


def test_canonical_stream_roots_are_siblings(tmp_path: Path) -> None:
    assert forcing_stream_root(tmp_path, "nrt") == tmp_path / "forcing/outputs/conus/nrt"
    assert forcing_stream_root(tmp_path, "retro") == tmp_path / "forcing/outputs/conus/retro"


def test_baseline_root_is_canonical(tmp_path: Path) -> None:
    assert baseline_root(tmp_path) == tmp_path / "forcing/outputs/conus/baseline"


def test_stream_root_must_match_selected_stream(tmp_path: Path) -> None:
    assert validate_stream_output_root(tmp_path / "product/nrt", "nrt").name == "nrt"
    with pytest.raises(ValueError, match="must end"):
        validate_stream_output_root(tmp_path / "product/retro", "nrt")
    with pytest.raises(ValueError, match="cannot be published"):
        validate_stream_output_root(tmp_path / "retro/archive/nrt", "nrt")
