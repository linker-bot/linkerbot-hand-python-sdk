"""Immutable aggregation of already acquired tactile quantities (no I/O)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ._force_names import FORCE_FIELDS
from .errors import DeviceNotReadyError
from .readings import ForceSample

if TYPE_CHECKING:
    from ..capabilities.types import DeviceCapabilities


def merge_force_samples(
    previous: ForceSample | None, current: ForceSample
) -> ForceSample:
    """Merge complete queries, retaining each field's original metadata."""
    if previous is None:
        return current
    if previous.meta.connection_generation != current.meta.connection_generation:
        raise DeviceNotReadyError("force queries crossed a connection generation")
    fields = {field.name: field for field in previous.fields}
    # A shared read replaces every quantity that could belong to that query.
    fields = {
        name: field
        for name, field in fields.items()
        if field.query_channel != current.query_channel
    }
    fields.update((field.name, field) for field in current.fields)
    unsupported = set(previous.unsupported_fields) | set(current.unsupported_fields)
    unsupported.difference_update(fields)
    return replace(
        current,
        # Preserve the original matrix/descriptor view in snapshots that also
        # receive independent scalar queries. Named fields carry scalar data.
        raw=previous.raw
        if (previous.tactile or previous.sensors)
        and not (current.tactile or current.sensors)
        else current.raw,
        tactile=current.tactile or previous.tactile,
        sensors=current.sensors or previous.sensors,
        fields=tuple(fields[name] for name in FORCE_FIELDS if name in fields),
        unsupported_fields=tuple(name for name in FORCE_FIELDS if name in unsupported),
    )


def age_force_fields(
    sample: ForceSample,
    completed_at: dict[str, float],
    *,
    now: float,
    capabilities: DeviceCapabilities,
) -> ForceSample:
    """Include time spent waiting for later queries in an active aggregate."""
    from ..capabilities.values import TelemetrySemantic

    metadata = {}
    for field in sample.fields:
        if not field.meta.valid or field.query_channel in metadata:
            continue
        assert field.meta.age_s is not None
        age = field.meta.age_s + max(0, now - completed_at[field.query_channel])
        telemetry = capabilities.telemetry_channel(
            TelemetrySemantic(field.query_channel)
        )
        metadata[field.query_channel] = replace(
            field.meta,
            age_s=age,
            stale=telemetry is None or age > telemetry.stale_after_s,
        )
    return replace(
        sample,
        fields=tuple(
            replace(field, meta=metadata[field.query_channel])
            if field.meta.valid
            else field
            for field in sample.fields
        ),
    )
