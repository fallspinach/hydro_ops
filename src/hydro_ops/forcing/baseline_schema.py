"""Versioned auxiliary schema; legacy absence is unknown, never zero QC."""
import numpy as np
from netCDF4 import default_fillvals

VERSION = "1"
FIELDS = frozenset(["RAINRATE", "T2D", "Q2D", "PSFC", "SWDOWN", "LWDOWN", "U2D", "V2D"])
SPECS = {
    "gfs_fallback_qc": ("u1", False, {"flag_masks": np.array([1, 2, 4, 8, 16, 32], "u1"), "flag_meanings": "gfs_meteorology gfs_precipitation model_HGT_used relative_humidity_clipped source_hour_roundoff_clipped active_hole_repaired"}),
    "gfs_forecast_reference_time": ("f8", True, {}),
    "gfs_forecast_lead_hours": ("i2", True, {"units": "hours"}),
    "native_donor_qc": ("u1", False, {"flag_masks": np.array([1, 2, 4, 8, 16, 32], "u1"), "flag_meanings": "coupled_thermodynamics wind shortwave precipitation model_terrain relative_humidity_clipped"}),
    "native_donor_distance_km": ("f4", False, {"units": "km"}),
}


def canonical_names(dataset):
    if not FIELDS <= set(dataset.variables):
        return set()
    names = set(SPECS)
    if "precip_source_id" in dataset.variables:
        names.add("precip_timing_source_id")
    return names


class UnknownDiagnostic:
    """Lightweight virtual variable without reading a full physical field."""

    def __init__(self, dataset, name):
        dtype, hourly, attrs = SPECS[name]
        self.dtype = np.dtype(dtype)
        self.dimensions = ("time",) if hourly else dataset["T2D"].dimensions
        self.shape = tuple(len(dataset.dimensions[d]) for d in self.dimensions)
        self.ndim = len(self.shape)
        self.attrs = {**attrs, "_FillValue": default_fillvals[dtype],
                      "comment": "Missing means unknown legacy provenance; zero, when recorded, means no action/lead/distance."}
        if name == "gfs_forecast_reference_time":
            self.attrs.update(_FillValue=np.nan, units=dataset["time"].units,
                              calendar=getattr(dataset["time"], "calendar", "standard"))

    def ncattrs(self):
        return list(self.attrs)

    def getncattr(self, name):
        return self.attrs[name]

    def __getattr__(self, name):
        if name in self.attrs:
            return self.attrs[name]
        raise AttributeError(name)

    def __getitem__(self, key):
        shape = np.broadcast_to(np.empty((), self.dtype), self.shape)[key].shape
        values = np.ma.masked_all(shape, dtype=self.dtype)
        return values[()] if shape == () else values


class CanonicalDiagnostic:
    """Normalize documented metadata, never reinterpret values or wrong units."""

    def __init__(self, dataset, name):
        self.reference = dataset[name]
        self.attrs = {k: self.reference.getncattr(k) for k in self.reference.ncattrs()}
        if name == "gfs_forecast_reference_time" and "units" not in self.attrs:
            raise ValueError("Missing GFS reference time epoch; cannot infer units")
        expected = UnknownDiagnostic(dataset, name)
        for key, value in expected.attrs.items():
            if key in {"_FillValue", "comment"}:
                continue
            if key in self.attrs and not np.array_equal(self.attrs[key], value):
                raise ValueError(f"Conflicting diagnostic metadata: {name}/{key}")
            self.attrs[key] = value
        self.attrs["comment"] = expected.attrs["comment"]

    def ncattrs(self):
        return list(self.attrs)

    def getncattr(self, name):
        return self.attrs[name]

    def __getattr__(self, name):
        if name in self.attrs:
            return self.attrs[name]
        return getattr(self.reference, name)

    def __getitem__(self, key):
        return self.reference[key]
