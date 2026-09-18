"""Descriptor-driven O30 HOP 0.0.4 tactile sensor transactions."""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import replace

from ...api.enums import DataOrigin
from ...api.errors import (
    ProtocolError,
    UnsupportedFirmwareError,
    UnsupportedOperationError,
)
from ...api.readings import (
    TactileFieldDescriptor,
    TactileFieldSample,
    TactileSensorMetadata,
    TactileSensorSample,
)
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    MI_SENSOR_DATA_1,
    MI_SENSOR_DATA_2,
    MI_SENSOR_DATA_3,
    MI_SENSOR_INFO,
)
from .framing import (
    MAX_FRAGMENT_LENGTH,
    HOPAddressing,
    build_read_message,
    build_write_message,
)
from .response import HOPRangeAssembler

SENSOR_NAMES = ("thumb", "index", "middle", "ring", "pinky")

_SI_SENSOR_TYPE = 0x00
_SI_DATA_UNIT = 0x20
_SI_DATA_RANGE = 0x28
_SI_MAXIMUM_ROWS = 0x29
_SI_MAXIMUM_COLUMNS = 0x2A
_SI_DESCRIPTOR_LENGTH = 0x2B
_SI_DESCRIPTOR = 0x2C
_SI_TOTAL_DATA_LENGTH = 0x6C
_SI_SELECTED_SENSOR = 0x6E
_SI_REQUESTED_ROWS = 0x6F
_SI_REQUESTED_COLUMNS = 0x70
_SI_VALID_DATA_POSITION_MODE = 0x71
_SI_RECTANGLE_FILL = 0x72

_METADATA_PREFIX_LENGTH = _SI_DESCRIPTOR - _SI_SENSOR_TYPE
_MAX_DESCRIPTOR_LENGTH = _SI_TOTAL_DATA_LENGTH - _SI_DESCRIPTOR
_SENSOR_CONTROL_LENGTH = 5
_MAX_SENSOR_DATA_LENGTH = 765
_STAGE_ESTIMATE_S = 0.003
_RESPONSE_TIMEOUT_S = 0.1
_LATE_RESPONSE_GUARD_S = 0.01

_SEMANTICS = frozenset(
    {"Fn", "Ft", "Fd", "Px", "FnS", "FtS", "FdS", "Rsv", "x", "y", "z", "xyz"}
)
_DATA_TYPES = {
    "U8": ("B", 1),
    "I8": ("b", 1),
    "U16": ("H", 2),
    "I16": ("h", 2),
    "U32": ("I", 4),
    "I32": ("i", 4),
    "F32": ("f", 4),
}

_NextFactory = Callable[[], ProtocolTransaction | None]


def parse_tactile_descriptor(
    descriptor_text: str,
    *,
    total_data_length: int | None = None,
) -> tuple[TactileFieldDescriptor, ...]:
    """Parse one 0.0.4 descriptor and calculate every byte range."""

    if not isinstance(descriptor_text, str) or not descriptor_text:
        raise ProtocolError("O30 tactile descriptor must be a non-empty string")
    if total_data_length is not None and (
        type(total_data_length) is not int or total_data_length < 0
    ):
        raise ProtocolError("O30 tactile total data length must be non-negative")

    fields: list[TactileFieldDescriptor] = []
    byte_offset = 0
    for item in descriptor_text.split(";"):
        parts = item.split("_")
        if len(parts) != 3:
            raise ProtocolError(f"O30 tactile descriptor field {item!r} is malformed")
        semantic, data_type, count_text = parts
        if semantic not in _SEMANTICS:
            raise ProtocolError(
                f"O30 tactile descriptor semantic {semantic!r} is unsupported"
            )
        try:
            _format, scalar_size = _DATA_TYPES[data_type]
        except KeyError as error:
            raise ProtocolError(
                f"O30 tactile descriptor data type {data_type!r} is unsupported"
            ) from error
        if (
            not count_text.isascii()
            or not count_text.isdecimal()
            or count_text.startswith("0")
        ):
            raise ProtocolError(
                f"O30 tactile descriptor count {count_text!r} is invalid"
            )
        element_count = int(count_text)
        scalar_count = element_count * (3 if semantic == "xyz" else 1)
        byte_length = scalar_count * scalar_size
        fields.append(
            TactileFieldDescriptor(
                semantic=semantic,
                data_type=data_type,
                element_count=element_count,
                byte_offset=byte_offset,
                byte_length=byte_length,
            )
        )
        byte_offset += byte_length

    if total_data_length is not None and byte_offset != total_data_length:
        raise ProtocolError(
            "O30 tactile descriptor defines "
            f"{byte_offset} bytes but device reports {total_data_length}"
        )
    return tuple(fields)


def decode_tactile_fields(
    descriptors: tuple[TactileFieldDescriptor, ...],
    raw_data: bytes,
) -> tuple[TactileFieldSample, ...]:
    """Decode exact little-endian values while preserving the source bytes."""

    if not isinstance(descriptors, tuple) or not descriptors:
        raise ProtocolError("O30 tactile field descriptors are empty")
    if any(not isinstance(value, TactileFieldDescriptor) for value in descriptors):
        raise ProtocolError("O30 tactile descriptors contain an invalid field")
    if not isinstance(raw_data, bytes):
        raise ProtocolError("O30 tactile data must be bytes")
    expected_length = sum(value.byte_length for value in descriptors)
    if len(raw_data) != expected_length:
        raise ProtocolError(
            f"O30 tactile data contains {len(raw_data)} bytes; "
            f"descriptor requires {expected_length}"
        )

    samples: list[TactileFieldSample] = []
    for descriptor in descriptors:
        format_code, _scalar_size = _DATA_TYPES[descriptor.data_type]
        scalar_count = descriptor.element_count * (
            3 if descriptor.semantic == "xyz" else 1
        )
        values = struct.unpack_from(
            f"<{scalar_count}{format_code}",
            raw_data,
            descriptor.byte_offset,
        )
        if any(
            isinstance(value, float) and not math.isfinite(value) for value in values
        ):
            raise ProtocolError("O30 tactile F32 data must contain finite values")
        decoded_values = (
            tuple(
                (values[index], values[index + 1], values[index + 2])
                for index in range(0, len(values), 3)
            )
            if descriptor.semantic == "xyz"
            else tuple(values)
        )
        samples.append(
            TactileFieldSample(
                semantic=descriptor.semantic,
                data_type=descriptor.data_type,
                element_count=descriptor.element_count,
                byte_offset=descriptor.byte_offset,
                byte_length=descriptor.byte_length,
                values=decoded_values,
            )
        )
    return tuple(samples)


def sensor_data_chunks(total_data_length: int) -> tuple[tuple[int, int, int], ...]:
    """Return ``(main_index, sub_index, length)`` chunks for global sensor bytes."""

    if type(total_data_length) is not int or not (
        1 <= total_data_length <= _MAX_SENSOR_DATA_LENGTH
    ):
        raise ProtocolError(
            f"O30 tactile data length must be in 1..{_MAX_SENSOR_DATA_LENGTH}"
        )
    chunks: list[tuple[int, int, int]] = []
    global_offset = 0
    while global_offset < total_data_length:
        if global_offset < 255:
            main_index, channel_start, channel_end = MI_SENSOR_DATA_1, 0, 255
        elif global_offset < 510:
            main_index, channel_start, channel_end = MI_SENSOR_DATA_2, 255, 510
        else:
            main_index, channel_start, channel_end = MI_SENSOR_DATA_3, 510, 765
        length = min(
            MAX_FRAGMENT_LENGTH,
            total_data_length - global_offset,
            channel_end - global_offset,
        )
        chunks.append((main_index, global_offset - channel_start, length))
        global_offset += length
    return tuple(chunks)


def tactile_transaction(
    capabilities: DeviceCapabilities,
    addressing: HOPAddressing,
    *,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    """Read all five O30 fingertip sensors as one atomic force sample."""

    telemetry = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError("O30 has no active tactile query")
    if set(metadata_cache) == set(range(1, len(SENSOR_NAMES) + 1)):
        return replace(
            _data_transaction(metadata_cache, addressing=addressing),
            firmware_optional=True,
        )

    metadata: dict[int, TactileSensorMetadata] = {}
    first = _metadata_select_transaction(
        1,
        metadata,
        addressing=addressing,
        metadata_cache=metadata_cache,
    )
    return replace(first, firmware_optional=True)


def _metadata_select_transaction(
    selected_sensor: int,
    metadata: dict[int, TactileSensorMetadata],
    *,
    addressing: HOPAddressing,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    payload = bytes((selected_sensor,))

    def next_stage() -> ProtocolTransaction:
        return _metadata_prefix_transaction(
            selected_sensor,
            metadata,
            addressing=addressing,
            metadata_cache=metadata_cache,
        )

    return _write_stage(
        key=("read", TelemetrySemantic.FORCE, "select_metadata", selected_sensor),
        sub_index=_SI_SELECTED_SENSOR,
        payload=payload,
        addressing=addressing,
        next_factory=next_stage,
    )


def _metadata_prefix_transaction(
    selected_sensor: int,
    metadata: dict[int, TactileSensorMetadata],
    *,
    addressing: HOPAddressing,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    values: dict[str, bytes] = {}

    def consume_prefix(data: bytes) -> None:
        values["prefix"] = data
        descriptor_length = data[_SI_DESCRIPTOR_LENGTH]
        if descriptor_length > _MAX_DESCRIPTOR_LENGTH:
            raise ProtocolError(
                f"O30 tactile descriptor length {descriptor_length} exceeds 64"
            )

    def next_stage() -> ProtocolTransaction:
        descriptor_length = values["prefix"][_SI_DESCRIPTOR_LENGTH]
        if descriptor_length == 0:
            values["descriptor"] = b""
            return _metadata_total_length_transaction(
                selected_sensor,
                metadata,
                values,
                addressing=addressing,
                metadata_cache=metadata_cache,
            )
        return _metadata_descriptor_transaction(
            selected_sensor,
            metadata,
            values,
            descriptor_length=descriptor_length,
            addressing=addressing,
            metadata_cache=metadata_cache,
        )

    return _read_stage(
        key=("read", TelemetrySemantic.FORCE, "metadata_prefix", selected_sensor),
        main_index=MI_SENSOR_INFO,
        sub_index=_SI_SENSOR_TYPE,
        length=_METADATA_PREFIX_LENGTH,
        on_data=consume_prefix,
        addressing=addressing,
        next_factory=next_stage,
    )


def _metadata_descriptor_transaction(
    selected_sensor: int,
    metadata: dict[int, TactileSensorMetadata],
    values: dict[str, bytes],
    *,
    descriptor_length: int,
    addressing: HOPAddressing,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    def consume_descriptor(data: bytes) -> None:
        values["descriptor"] = data

    def next_stage() -> ProtocolTransaction:
        return _metadata_total_length_transaction(
            selected_sensor,
            metadata,
            values,
            addressing=addressing,
            metadata_cache=metadata_cache,
        )

    return _read_stage(
        key=("read", TelemetrySemantic.FORCE, "descriptor", selected_sensor),
        main_index=MI_SENSOR_INFO,
        sub_index=_SI_DESCRIPTOR,
        length=descriptor_length,
        on_data=consume_descriptor,
        addressing=addressing,
        next_factory=next_stage,
    )


def _metadata_total_length_transaction(
    selected_sensor: int,
    metadata: dict[int, TactileSensorMetadata],
    values: dict[str, bytes],
    *,
    addressing: HOPAddressing,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    def consume_total_length(data: bytes) -> None:
        values["total_data_length"] = data

    def next_stage() -> ProtocolTransaction:
        return _metadata_controls_transaction(
            selected_sensor,
            metadata,
            values,
            addressing=addressing,
            metadata_cache=metadata_cache,
        )

    return _read_stage(
        key=("read", TelemetrySemantic.FORCE, "total_length", selected_sensor),
        main_index=MI_SENSOR_INFO,
        sub_index=_SI_TOTAL_DATA_LENGTH,
        length=2,
        on_data=consume_total_length,
        addressing=addressing,
        next_factory=next_stage,
    )


def _metadata_controls_transaction(
    selected_sensor: int,
    metadata: dict[int, TactileSensorMetadata],
    values: dict[str, bytes],
    *,
    addressing: HOPAddressing,
    metadata_cache: dict[int, TactileSensorMetadata],
) -> ProtocolTransaction:
    final_sensor = selected_sensor == len(SENSOR_NAMES)

    def consume_controls(data: bytes) -> None:
        parsed = _build_metadata(selected_sensor, values, data)
        metadata[selected_sensor] = parsed
        if final_sensor:
            metadata_cache.clear()
            metadata_cache.update(metadata)

    def next_stage() -> ProtocolTransaction:
        if not final_sensor:
            return _metadata_select_transaction(
                selected_sensor + 1,
                metadata,
                addressing=addressing,
                metadata_cache=metadata_cache,
            )
        return _data_transaction(metadata, addressing=addressing)

    return _read_stage(
        key=("read", TelemetrySemantic.FORCE, "controls", selected_sensor),
        main_index=MI_SENSOR_INFO,
        sub_index=_SI_SELECTED_SENSOR,
        length=_SENSOR_CONTROL_LENGTH,
        on_data=consume_controls,
        addressing=addressing,
        next_factory=next_stage,
    )


def _build_metadata(
    selected_sensor: int,
    values: dict[str, bytes],
    controls: bytes,
) -> TactileSensorMetadata:
    prefix = values["prefix"]
    sensor_type = _decode_padded_text(prefix[:_SI_DATA_UNIT], "sensor type")
    if sensor_type is None:
        raise ProtocolError("O30 tactile sensor type is empty")
    data_unit = _decode_padded_text(
        prefix[_SI_DATA_UNIT:_SI_DATA_RANGE],
        "data unit",
    )
    descriptor_data = values["descriptor"]
    descriptor_text = _decode_descriptor(descriptor_data)
    total_data_length = int.from_bytes(values["total_data_length"], "little")
    if controls[0] != selected_sensor:
        raise ProtocolError(
            f"O30 selected sensor changed from {selected_sensor} to {controls[0]}"
        )
    valid_data_position_mode = _decode_switch(
        controls[_SI_VALID_DATA_POSITION_MODE - _SI_SELECTED_SENSOR],
        "valid data position mode",
    )
    rectangle_fill = _decode_switch(
        controls[_SI_RECTANGLE_FILL - _SI_SELECTED_SENSOR],
        "rectangle fill",
    )

    if descriptor_text is None:
        if total_data_length != 0:
            raise ProtocolError("O30 tactile sensor reports data without a descriptor")
        if sensor_type != "NO_SENSOR":
            raise ProtocolError("O30 installed tactile sensor has no descriptor")
        descriptors: tuple[TactileFieldDescriptor, ...] = ()
    else:
        if sensor_type == "NO_SENSOR":
            raise ProtocolError("O30 NO_SENSOR metadata contains a descriptor")
        descriptors = parse_tactile_descriptor(
            descriptor_text,
            total_data_length=total_data_length,
        )
        if total_data_length > _MAX_SENSOR_DATA_LENGTH:
            raise ProtocolError("O30 tactile data exceeds three 255-byte pages")
    return TactileSensorMetadata(
        selected_sensor=selected_sensor,
        sensor_name=SENSOR_NAMES[selected_sensor - 1],
        sensor_type=sensor_type,
        data_unit=data_unit,
        data_range=prefix[_SI_DATA_RANGE],
        maximum_rows=prefix[_SI_MAXIMUM_ROWS],
        maximum_columns=prefix[_SI_MAXIMUM_COLUMNS],
        requested_rows=controls[_SI_REQUESTED_ROWS - _SI_SELECTED_SENSOR],
        requested_columns=controls[_SI_REQUESTED_COLUMNS - _SI_SELECTED_SENSOR],
        valid_data_position_mode=valid_data_position_mode,
        rectangle_fill=rectangle_fill,
        descriptor_text=descriptor_text,
        field_descriptors=descriptors,
        total_data_length=total_data_length,
    )


def _data_transaction(
    metadata: dict[int, TactileSensorMetadata],
    *,
    addressing: HOPAddressing,
) -> ProtocolTransaction:
    available = tuple(
        selected_sensor
        for selected_sensor in range(1, len(SENSOR_NAMES) + 1)
        if metadata[selected_sensor].available
    )
    if not available:
        raise UnsupportedFirmwareError("O30 reports no available fingertip sensors")
    # Keep five stable identities. An absent sensor has no bytes or fields;
    # it is distinct from a present sensor that measured zero force.
    samples = {
        index: TactileSensorSample(metadata=value, raw_data=b"", fields=())
        for index, value in metadata.items()
        if not value.available
    }
    return _data_select_transaction(
        available,
        0,
        metadata,
        samples,
        addressing=addressing,
    )


def _data_select_transaction(
    available: tuple[int, ...],
    sensor_position: int,
    metadata: dict[int, TactileSensorMetadata],
    samples: dict[int, TactileSensorSample],
    *,
    addressing: HOPAddressing,
) -> ProtocolTransaction:
    selected_sensor = available[sensor_position]
    payload = bytes((selected_sensor,))

    def next_stage() -> ProtocolTransaction:
        return _data_chunk_transaction(
            available,
            sensor_position,
            metadata,
            samples,
            chunks=sensor_data_chunks(metadata[selected_sensor].total_data_length),
            chunk_position=0,
            raw_data=bytearray(metadata[selected_sensor].total_data_length),
            global_offset=0,
            addressing=addressing,
        )

    return _write_stage(
        key=("read", TelemetrySemantic.FORCE, "select_data", selected_sensor),
        sub_index=_SI_SELECTED_SENSOR,
        payload=payload,
        addressing=addressing,
        next_factory=next_stage,
    )


def _data_chunk_transaction(
    available: tuple[int, ...],
    sensor_position: int,
    metadata: dict[int, TactileSensorMetadata],
    samples: dict[int, TactileSensorSample],
    *,
    chunks: tuple[tuple[int, int, int], ...],
    chunk_position: int,
    raw_data: bytearray,
    global_offset: int,
    addressing: HOPAddressing,
) -> ProtocolTransaction:
    selected_sensor = available[sensor_position]
    main_index, sub_index, length = chunks[chunk_position]
    final_chunk = chunk_position + 1 == len(chunks)
    final_sensor = sensor_position + 1 == len(available)

    def consume_chunk(data: bytes) -> SamplePayload | None:
        raw_data[global_offset : global_offset + length] = data
        if not final_chunk:
            return None
        sensor_metadata = metadata[selected_sensor]
        exact_data = bytes(raw_data)
        samples[selected_sensor] = TactileSensorSample(
            metadata=sensor_metadata,
            raw_data=exact_data,
            fields=decode_tactile_fields(
                sensor_metadata.field_descriptors,
                exact_data,
            ),
        )
        if not final_sensor:
            return None
        return SamplePayload(
            semantic=TelemetrySemantic.FORCE,
            sensors=tuple(samples[index] for index in range(1, len(SENSOR_NAMES) + 1)),
            origin=DataOrigin.DEVICE_RESPONSE,
        )

    def next_stage() -> ProtocolTransaction:
        if not final_chunk:
            return _data_chunk_transaction(
                available,
                sensor_position,
                metadata,
                samples,
                chunks=chunks,
                chunk_position=chunk_position + 1,
                raw_data=raw_data,
                global_offset=global_offset + length,
                addressing=addressing,
            )
        return _data_select_transaction(
            available,
            sensor_position + 1,
            metadata,
            samples,
            addressing=addressing,
        )

    has_next = not (final_chunk and final_sensor)
    return _read_stage(
        key=(
            "read",
            TelemetrySemantic.FORCE,
            "data",
            selected_sensor,
            chunk_position,
        ),
        main_index=main_index,
        sub_index=sub_index,
        length=length,
        on_data=consume_chunk,
        addressing=addressing,
        next_factory=next_stage if has_next else None,
    )


def _read_stage(
    *,
    key: object,
    main_index: int,
    sub_index: int,
    length: int,
    on_data: Callable[[bytes], SamplePayload | None] | Callable[[bytes], None],
    addressing: HOPAddressing,
    next_factory: _NextFactory | None,
) -> ProtocolTransaction:
    assembler = HOPRangeAssembler(
        main_index,
        sub_index=sub_index,
        length=length,
        rts=False,
        response_id=addressing.response_id,
        object_unavailable_is_unsupported=True,
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        result = on_data(complete)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=result,
        )

    return ProtocolTransaction(
        key=key,
        correlation_key=("o30_hop", addressing.response_id),
        request_messages=(
            build_read_message(
                main_index,
                sub_index=sub_index,
                length=length,
                request_id=addressing.request_id,
            ),
        ),
        response_timeout_s=_RESPONSE_TIMEOUT_S,
        estimated_duration_s=_STAGE_ESTIMATE_S,
        response_consumer=consume,
        late_response_guard_s=_LATE_RESPONSE_GUARD_S,
        next_transaction_factory=next_factory,
        yield_after_response=next_factory is not None,
    )


def _write_stage(
    *,
    key: object,
    sub_index: int,
    payload: bytes,
    addressing: HOPAddressing,
    next_factory: _NextFactory,
) -> ProtocolTransaction:
    assembler = HOPRangeAssembler(
        MI_SENSOR_INFO,
        sub_index=sub_index,
        length=len(payload),
        rts=False,
        response_id=addressing.response_id,
        object_unavailable_is_unsupported=True,
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        if complete != payload:
            raise ProtocolError("O30 tactile configuration response did not echo value")
        return ResponseProgress(matched=True, complete=True)

    return ProtocolTransaction(
        key=key,
        correlation_key=("o30_hop", addressing.response_id),
        request_messages=(
            build_write_message(
                MI_SENSOR_INFO,
                sub_index=sub_index,
                payload=payload,
                request_id=addressing.request_id,
            ),
        ),
        response_timeout_s=_RESPONSE_TIMEOUT_S,
        estimated_duration_s=_STAGE_ESTIMATE_S,
        response_consumer=consume,
        late_response_guard_s=_LATE_RESPONSE_GUARD_S,
        next_transaction_factory=next_factory,
        yield_after_response=True,
    )


def _decode_padded_text(data: bytes, label: str) -> str | None:
    try:
        value = data.split(b"\x00", 1)[0].decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ProtocolError(f"O30 tactile {label} is not UTF-8") from error
    return value or None


def _decode_descriptor(data: bytes) -> str | None:
    if not data:
        return None
    try:
        value = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise ProtocolError("O30 tactile descriptor is not ASCII") from error
    if not value or "\x00" in value or value.strip() != value:
        raise ProtocolError("O30 tactile descriptor contains invalid padding")
    return value


def _decode_switch(value: int, label: str) -> bool:
    if value not in {0, 1}:
        raise ProtocolError(f"O30 tactile {label} must be zero or one")
    return bool(value)


__all__ = [
    "SENSOR_NAMES",
    "decode_tactile_fields",
    "parse_tactile_descriptor",
    "sensor_data_chunks",
    "tactile_transaction",
]
