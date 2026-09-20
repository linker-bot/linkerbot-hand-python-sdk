"""Project exact matrix/descriptor/scalar payloads once per publication."""

from __future__ import annotations

from typing import cast

from ..api._force_names import FORCE_FIELDS
from ..api.readings import ForceFieldSample, ForceSample, RawNumber, SampleMeta
from ..capabilities.types import DeviceCapabilities
from ..protocols.base import SamplePayload


def build_force_sample(
    payload: SamplePayload, meta: SampleMeta, capabilities: DeviceCapabilities
) -> ForceSample:
    telemetry = capabilities.telemetry_channel(payload.semantic)
    assert telemetry is not None
    fields: list[ForceFieldSample] = []
    query = payload.semantic.value
    if payload.sensors:
        for name in FORCE_FIELDS:
            matches = tuple(
                tuple(
                    field for field in sensor.fields if field.semantic.lower() == name
                )
                for sensor in payload.sensors
            )
            if not any(matches):
                continue
            values = tuple(
                tuple(
                    cast(RawNumber, value) for field in group for value in field.values
                )
                if group
                else None
                for group in matches
            )
            fields.append(
                ForceFieldSample(
                    name=name,
                    sensor_names=tuple(
                        sensor.metadata.sensor_name for sensor in payload.sensors
                    ),
                    raw=values,
                    units=tuple(
                        sensor.metadata.data_unit
                        if group and name in {"fn", "fns", "ft", "fts"}
                        else None
                        for sensor, group in zip(payload.sensors, matches, strict=True)
                    ),
                    encodings=tuple(
                        ";".join(field.data_type for field in group) if group else None
                        for group in matches
                    ),
                    valid_values=tuple(
                        None if values is None else (True,) * len(values)
                        for values in values
                    ),
                    meta=meta,
                    query_channel=query,
                    scale_note=(
                        "Exact descriptor values, little-endian multibyte encoding; "
                        "force units are device-reported sensor units, without conversion. "
                        "Direction/proximity units are not inferred from the force unit."
                    ),
                )
            )
        unsupported = tuple(
            name
            for name in FORCE_FIELDS
            if not any(field.name == name for field in fields)
        )
    else:
        names = telemetry.force_fields or (("fn",) if payload.tactile else ())
        for name in names:
            values = (
                tuple(
                    tuple(value for row in matrix.raw for value in row)
                    for matrix in payload.tactile
                )
                if payload.tactile
                else tuple((value,) for value in payload.raw)
            )
            fields.append(
                ForceFieldSample(
                    name=name,
                    sensor_names=telemetry.channels,
                    raw=values,
                    units=(telemetry.raw_unit,) * len(values),
                    encodings=(telemetry.raw_encoding,) * len(values),
                    valid_values=tuple(
                        tuple(
                            (
                                not payload.tactile
                                or payload.tactile[index].online is not False
                            )
                            and (name != "fds" or value != 0xFF)
                            for value in finger
                        )
                        for index, finger in enumerate(values)
                    ),
                    meta=meta,
                    query_channel=query,
                    scale_note=telemetry.raw_scale_note,
                )
            )
        unsupported = tuple(
            field.name
            for field in capabilities.force_fields
            if field.supported is False
        )
    return ForceSample(
        raw=payload.raw,
        tactile=payload.tactile,
        sensors=payload.sensors,
        meta=meta,
        fields=tuple(fields),
        unsupported_fields=unsupported,
        query_channel=query,
    )
