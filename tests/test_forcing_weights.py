from pathlib import Path

import pytest

from hydro_ops.forcing.weights import build_weight_command


def test_build_weight_command() -> None:
    command = build_weight_command(
        "/usr/bin/cdo",
        Path("source.nc"),
        Path("target.nc"),
        Path("weights.part.nc"),
        method="bilinear",
        variable="Tair",
    )
    assert command == [
        "/usr/bin/cdo",
        "-O",
        "genbil,target.nc",
        "-selname,Tair",
        "source.nc",
        "weights.part.nc",
    ]


def test_build_weight_command_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="Unknown remapping method"):
        build_weight_command(
            "cdo",
            Path("source.nc"),
            Path("target.nc"),
            Path("weights.nc"),
            method="spline",
            variable="Tair",
        )
