"""Opt-in, distance-bounded native-source repair after ordinary remapping."""

import numpy as np
from netCDF4 import Dataset
from scipy.spatial import cKDTree

from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.physics import lambert_grid_x_angle, rotate_grid_to_earth
from hydro_ops.forcing.thermodynamics import finalize_target_state, prepare_reference_state

FIELDS = {"T2D": "air_temperature", "Q2D": "specific_humidity", "PSFC": "surface_pressure",
          "LWDOWN": "downward_longwave", "SWDOWN": "downward_shortwave",
          "U2D": "wind_u", "V2D": "wind_v", "RAINRATE": "precipitation_depth"}


def xyz(lat, lon):
    la, lo = np.deg2rad(lat), np.deg2rad(lon)
    return np.column_stack((np.cos(la)*np.cos(lo), np.cos(la)*np.sin(lo), np.sin(la)))


def nearest_native(lat, lon, valid, target_lat, target_lon, maximum_km=25):
    """Great-circle nearest donor, with a hard maximum (never fill beyond it)."""
    if not np.isfinite(maximum_km) or maximum_km <= 0:
        raise ValueError("Invalid native donor distance limit")
    eligible = valid & np.isfinite(lat) & np.isfinite(lon)
    indices = np.flatnonzero(eligible)
    if not len(indices):
        raise ValueError("No valid native donors")
    chord, address = cKDTree(xyz(lat.ravel()[indices], lon.ravel()[indices])).query(xyz(target_lat, target_lon))
    distance = 6371.0 * 2 * np.arcsin(np.clip(chord/2, 0, 1))
    if np.any(distance > maximum_km):
        raise ValueError(f"Native donor exceeds {maximum_km} km: maximum {distance.max():.3f} km")
    return indices[address], distance


def repair_arrays(primary, native, source_height, source_lat, source_lon,
                  target_height, target_lat, target_lon, active, *, maximum_km=25):
    """Return repaired copies and provenance; never change supported precipitation."""
    result = {name: np.asarray(values).copy() for name, values in primary.items()}
    flags = np.zeros(active.shape, np.uint8)
    distances = np.zeros(active.shape, np.float32)
    report = {}
    state = prepare_reference_state(native["T2D"], native["PSFC"], native["Q2D"], native["LWDOWN"],
                                    source_height, relative_humidity_tolerance=0.2,
                                    reject_material_rh_excursions=False)
    groups = ((1, ("T2D", "PSFC", "Q2D", "LWDOWN")), (2, ("U2D", "V2D")),
              (4, ("SWDOWN",)), (8, ("RAINRATE",)))
    for bit, names in groups:
        missing = active & np.logical_or.reduce([~np.isfinite(result[n]) for n in names])
        if not missing.any():
            report[names[0]] = {"cells": 0, "maximum_km": 0.0}
            continue
        valid = np.logical_and.reduce([np.isfinite(native[n]) for n in names])
        if bit == 1:
            valid &= np.isfinite(state.temperature) & np.isfinite(state.pressure)
            valid &= np.isfinite(state.relative_humidity) & np.isfinite(state.longwave_factor)
            if not np.isfinite(target_height[missing]).all():
                raise ValueError("Missing active target elevation")
        if bit in (4, 8):
            valid &= native[names[0]] >= 0
        address, distance = nearest_native(source_lat, source_lon, valid,
            target_lat[missing], target_lon[missing], maximum_km)
        if bit == 1:
            restored = finalize_target_state(state.temperature.ravel()[address], state.pressure.ravel()[address],
                state.relative_humidity.ravel()[address], state.longwave_factor.ravel()[address], target_height[missing],
                relative_humidity_tolerance=0.2)
            values = (restored.temperature, restored.pressure, restored.specific_humidity, restored.downward_longwave)
            clipped = ((state.qc_flags.ravel()[address] | restored.qc_flags) & 3) != 0
            flags[missing] |= np.where(clipped, 32, 0).astype(np.uint8)
        else:
            values = [native[n].ravel()[address] for n in names]
        for name, values_for_name in zip(names, values, strict=True):
            result[name][missing] = values_for_name
        flags[missing] |= bit
        distances[missing] = np.maximum(distances[missing], distance)
        report[names[0]] = {"cells": int(missing.sum()), "maximum_km": float(distance.max())}
    return result, flags, distances, report


class NativeDonorRepair:
    """Load invariant target geometry once; repair only new staged hourly files."""

    def __init__(self, layout, envelope, model_terrain, *, maximum_km=25):
        self.layout, self.maximum_km = layout, maximum_km
        with Dataset(envelope) as data:
            self.lat, self.lon = np.asarray(data["lat"][:]), np.asarray(data["lon"][:])
            self.keep, self.active = np.asarray(data["keep"][:], bool), np.asarray(data["active"][:], bool)
        with Dataset(layout.target_elevation) as data:
            self.height = np.ma.filled(data["elevation"][:], np.nan)
        with Dataset(model_terrain) as data:
            np.testing.assert_allclose(data["XLAT"][0], self.lat, atol=1e-5, rtol=0)
            np.testing.assert_allclose(data["XLONG"][0], self.lon, atol=1e-5, rtol=0)
            model_height = np.ma.filled(data["HGT"][0], np.nan)
        self.model_height_used = ~np.isfinite(self.height) & self.active
        self.height = np.where(self.model_height_used, model_height, self.height)

    def repair(self, path, selection):
        with open_normalized_forcing(selection.path, selection.product, valid_time=selection.valid_time) as source:
            native = {name: np.asarray(source[key].squeeze().values) for name, key in FIELDS.items()}
            native["RAINRATE"] = native["RAINRATE"] / 3600.0
            if selection.product == "nldas2":
                slon, slat = np.meshgrid(source.lon.values, source.lat.values)
                terrain, name = self.layout.nldas2_elevation, "NLDAS_elev"
            else:
                slat, slon = source.latitude.values, source.longitude.values
                native["U2D"], native["V2D"] = rotate_grid_to_earth(native["U2D"], native["V2D"],
                    lambert_grid_x_angle(slon, central_longitude=-97.5, standard_parallel_1=38.5))
                terrain, name = self.layout.hrrr_elevation, "HGT_surface"
            with Dataset(terrain) as data:
                height = np.ma.filled(data[name][:], np.nan).squeeze()
        with Dataset(path, "r+") as data:
            np.testing.assert_allclose(data["lat"][:], self.lat, atol=1e-5, rtol=0)
            np.testing.assert_allclose(data["lon"][:], self.lon, atol=1e-5, rtol=0)
            primary = {name: np.ma.filled(data[name][0], np.nan) for name in FIELDS}
            result, flags, distances, report = repair_arrays(primary, native, height, slat, slon,
                self.height, self.lat, self.lon, self.active, maximum_km=self.maximum_km)
            flags[self.model_height_used & ((flags & 1) != 0)] |= 16
            for name, values in result.items():
                values[~self.keep] = np.nan
                if not np.isfinite(values[self.active]).all():
                    raise ValueError(f"Unrepaired active {name}")
                data[name][0] = np.where(np.isfinite(values), values, data[name]._FillValue)
            dims = data["T2D"].dimensions
            qc = data.createVariable("native_donor_qc", "u1", dims, zlib=True, complevel=2)
            qc.flag_masks = np.array([1, 2, 4, 8, 16, 32], np.uint8)
            qc.flag_meanings = "coupled_thermodynamics wind shortwave precipitation model_terrain relative_humidity_clipped"
            qc[0] = flags
            distance = data.createVariable("native_donor_distance_km", "f4", dims, zlib=True, complevel=2)
            distance.units = "km"
            distance[0] = distances
            sid = 1 if selection.product == "nldas2" else 2
            ids = np.asarray(data["forcing_source_id"][0])
            ids[((flags & 7) != 0)] = sid
            ids[~self.keep] = 0
            data["forcing_source_id"][0] = ids
            mq = np.asarray(data["forcing_qc_flags"][0], np.uint32)
            mq[(flags & 7) != 0] &= np.uint32(0xffffffff ^ (4 | 8 | (4 << 16) | (1 << 31)))
            data["forcing_qc_flags"][0] = mq
            pq = np.asarray(data["precip_qc_flags"][0], np.uint16)
            pq[(flags & 8) != 0] = 4
            data["precip_qc_flags"][0] = pq
            pi = np.asarray(data["precip_source_id"][0])
            pi[(flags & 8) != 0] = 5 if sid == 1 else 6
            pi[~self.keep] = 0
            data["precip_source_id"][0] = pi
            data.native_donor_maximum_km = self.maximum_km
            data.native_donor_source = str(selection.path)
            data.forcing_source = selection.product
            data.forcing_domain_policy = "native_donor_static_envelope_v1"
        with Dataset(path) as data:
            for name in FIELDS:
                values = np.ma.filled(data[name][0], np.nan)
                if not np.isfinite(values[self.active]).all() or np.isfinite(values[~self.keep]).any():
                    raise ValueError(f"Native repair readback failed: {name}")
        return report
