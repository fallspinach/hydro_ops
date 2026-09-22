"""Separate model activity from forcing retention on a shared subset grid."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_subset import GridWindow


def mask_digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype='u1').tobytes()).hexdigest()


def derive_masks(boundary, active, keep):
    boundary, active, keep = [np.asarray(a, dtype=bool) for a in (boundary, active, keep)]
    if boundary.ndim != 2 or not (boundary.shape == active.shape == keep.shape):
        raise ValueError('Mask arrays must have matching 2-D shapes')
    model = boundary & active
    forcing = boundary & keep
    if np.any(model & ~forcing):
        raise ValueError('Active model cells lack forcing-envelope coverage')
    if not model.any():
        raise ValueError('Boundary contains no active model cells')
    return model, forcing


def load_masks(path: Path):
    with Dataset(path) as data:
        window = GridWindow(**json.loads(data.source_window))
        masks = {name: np.asarray(data[name][:], dtype=bool)
                 for name in ('boundary_mask', 'model_mask', 'forcing_mask')}
        for name, values in masks.items():
            if values.shape != window.shape or mask_digest(values) != data[name].sha256:
                raise ValueError(f'Invalid {name} shape/checksum')
        if np.any(masks['model_mask'] & ~masks['forcing_mask']):
            raise ValueError('Model mask exceeds forcing mask')
        if np.any(masks['forcing_mask'] & ~masks['boundary_mask']):
            raise ValueError('Forcing mask exceeds boundary')
        lat, lon = np.asarray(data['lat'][:]), np.asarray(data['lon'][:])
    return window, masks, lat, lon
