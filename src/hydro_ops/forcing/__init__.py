"""Scientific processing primitives for NWM meteorological forcing."""

from hydro_ops.forcing.assemble import add_precipitation_to_ldasin, assemble_seven_field_hour
from hydro_ops.forcing.evaluation import (
    categorical_precipitation_metrics,
    continuous_metrics,
    deterministic_group_split,
    stage4_override_sweep,
    stratified_metrics,
)
from hydro_ops.forcing.operations import produce_complete_hour
from hydro_ops.forcing.physics import (
    DEFAULT_LAPSE_RATE,
    adjust_temperature_range,
    cosgrove_atmospheric_emissivity,
    cosgrove_longwave_at_target,
    cosine_solar_zenith,
    lambert_grid_x_angle,
    pressure_at_elevation,
    relative_humidity_from_specific_humidity,
    rotate_grid_to_earth,
    saturation_vapor_pressure,
    specific_humidity_from_relative_humidity,
    temperature_at_elevation,
)
from hydro_ops.forcing.precipitation import composite_precipitation
from hydro_ops.forcing.precipitation_hour import process_precipitation_hour
from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
    reconcile_prism_day,
)
from hydro_ops.forcing.prism_temperature import (
    apply_daily_temperature_constraint,
    create_prism_temperature_constraints,
)
from hydro_ops.forcing.produce import produce_seven_field_hour
from hydro_ops.forcing.radiation_wind_hour import process_radiation_wind_hour
from hydro_ops.forcing.source_selection import select_hourly_source
from hydro_ops.forcing.thermodynamic_hour import process_thermodynamic_hour
from hydro_ops.forcing.thermodynamics import (
    ReferenceState,
    TargetState,
    ThermodynamicQC,
    finalize_target_state,
    prepare_reference_state,
)
from hydro_ops.forcing.weights import validate_weight_manifest

__all__ = [
    "DEFAULT_LAPSE_RATE",
    "ConservativeOperator",
    "ReconciliationQC",
    "ReferenceState",
    "TargetState",
    "ThermodynamicQC",
    "add_precipitation_to_ldasin",
    "adjust_temperature_range",
    "apply_daily_temperature_constraint",
    "assemble_seven_field_hour",
    "categorical_precipitation_metrics",
    "composite_precipitation",
    "continuous_metrics",
    "cosgrove_atmospheric_emissivity",
    "cosgrove_longwave_at_target",
    "cosine_solar_zenith",
    "create_prism_temperature_constraints",
    "deterministic_group_split",
    "finalize_target_state",
    "lambert_grid_x_angle",
    "prepare_reference_state",
    "pressure_at_elevation",
    "process_precipitation_hour",
    "process_radiation_wind_hour",
    "process_thermodynamic_hour",
    "produce_complete_hour",
    "produce_seven_field_hour",
    "reconcile_prism_day",
    "relative_humidity_from_specific_humidity",
    "rotate_grid_to_earth",
    "saturation_vapor_pressure",
    "select_hourly_source",
    "specific_humidity_from_relative_humidity",
    "stage4_override_sweep",
    "stratified_metrics",
    "temperature_at_elevation",
    "validate_weight_manifest",
]
