from hydro_ops.forcing.layout_migration import map_dated_forcing_path


def test_mapping_is_scoped_idempotent_and_keeps_summary_roots(tmp_path):
    source = 'forcing/outputs/cnrfc/retro/2020/01/file'
    expected = 'forcing/outputs/cnrfc/retro/hourly/2020/01/file'
    assert map_dated_forcing_path(source, tmp_path) == expected
    assert map_dated_forcing_path(expected, tmp_path) == expected
    assert map_dated_forcing_path(str(tmp_path/source), tmp_path) == str(tmp_path/expected)
    for path in ['forcing/outputs/conus/retro',
                 'forcing/outputs/conus/retro/daily/2020/01/file',
                 'forcing/outputs/conus/nrt/monthly/2020/file',
                 'forcing/inputs/noaa/hrrr/2020/file',
                 'forcing/outputs/conus/retro/experimental_x/file',
                 '/unrelated/forcing/outputs/conus/retro/2020/file']:
        assert map_dated_forcing_path(path, tmp_path) == path
