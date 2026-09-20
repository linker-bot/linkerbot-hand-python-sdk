"""Resolve user rates to deduplicated physical queries for every caller."""

from __future__ import annotations

from ..api._force_names import DEFAULT_FORCE_FIELDS, FORCE_FIELDS
from ..api.config import PollingPlan
from ..api.errors import UnsupportedFirmwareError, UnsupportedOperationError
from .types import DeviceCapabilities, TelemetrySemantic

POLL_CHANNELS = {
    "angles": TelemetrySemantic.ANGLES,
    "speeds": TelemetrySemantic.MEASURED_SPEEDS,
    "force": TelemetrySemantic.FORCE,
    "temperature": TelemetrySemantic.TEMPERATURES,
    "current": TelemetrySemantic.CURRENTS,
    "faults": TelemetrySemantic.FAULTS,
    "torques": TelemetrySemantic.TORQUES,
    "voltage": TelemetrySemantic.VOLTAGES,
}


def requested_force_rates(plan: PollingPlan) -> dict[str, float]:
    return {
        name: plan.force_hz
        if name in DEFAULT_FORCE_FIELDS and plan.force_hz > 0
        else getattr(plan, f"force_{name}_hz")
        for name in FORCE_FIELDS
    }


def resolve_poll_rates(
    plan: PollingPlan,
    capabilities: DeviceCapabilities,
    *,
    strict: bool = True,
) -> dict[TelemetrySemantic, float]:
    """Validate support and combine shared query rates with max, never sum."""
    result: dict[TelemetrySemantic, float] = {}
    for channel, rate in plan.enabled_rates():
        if channel == "force" or channel.startswith("force_"):
            continue
        semantic = POLL_CHANNELS[channel]
        telemetry = capabilities.telemetry_channel(semantic)
        if semantic in capabilities.firmware_unsupported_telemetry:
            if not strict:
                continue
            raise UnsupportedFirmwareError(
                f"{capabilities.model} firmware does not support polling {channel}"
            )
        if telemetry is None or not telemetry.device_queried:
            raise UnsupportedOperationError(
                f"{capabilities.model} cannot poll {channel}"
            )
        result[semantic] = rate
    fields = {field.name: field for field in capabilities.force_fields}
    for name, rate in requested_force_rates(plan).items():
        if rate <= 0:
            continue
        field = fields[name]
        if field.supported is False:
            default = plan.force_hz > 0 and name in DEFAULT_FORCE_FIELDS
            if default or not strict:
                continue
            error_type = (
                UnsupportedFirmwareError
                if field.firmware_unsupported
                else UnsupportedOperationError
            )
            raise error_type(f"{capabilities.model} cannot poll force_{name}")
        if field.query_channel is not None:
            result[field.query_channel] = max(result.get(field.query_channel, 0), rate)
    if (
        strict
        and plan.force_hz > 0
        and not any(
            fields[name].supported is not False for name in DEFAULT_FORCE_FIELDS
        )
    ):
        error_type = (
            UnsupportedFirmwareError
            if any(fields[name].firmware_unsupported for name in DEFAULT_FORCE_FIELDS)
            else UnsupportedOperationError
        )
        raise error_type(f"{capabilities.model} cannot poll force")
    return result
