"""Unit tests for the descriptor-driven O30 0.0.4 tactile primitives."""

from __future__ import annotations

import struct

import pytest

from linkerbot_hand.api.errors import ProtocolError
from linkerbot_hand.protocols.o30.codec import (
    MI_SENSOR_DATA_1,
    MI_SENSOR_DATA_2,
    MI_SENSOR_DATA_3,
)
from linkerbot_hand.protocols.o30.sensors import (
    decode_tactile_fields,
    parse_tactile_descriptor,
    sensor_data_chunks,
)


def test_parse_descriptor_computes_offsets_for_the_tssp_jzg_layout() -> None:
    fields = parse_tactile_descriptor("FnS_U16_1;Fn_U8_70", total_data_length=72)

    assert len(fields) == 2
    resultant, normal_force = fields
    assert (resultant.semantic, resultant.data_type) == ("FnS", "U16")
    assert (resultant.element_count, resultant.byte_offset, resultant.byte_length) == (
        1,
        0,
        2,
    )
    assert (normal_force.semantic, normal_force.data_type) == ("Fn", "U8")
    assert (
        normal_force.element_count,
        normal_force.byte_offset,
        normal_force.byte_length,
    ) == (70, 2, 70)


def test_parse_descriptor_treats_xyz_as_three_interleaved_scalars() -> None:
    (field,) = parse_tactile_descriptor("xyz_I16_4")

    assert field.element_count == 4
    assert field.byte_length == 4 * 3 * 2


def test_parse_descriptor_rejects_length_mismatch() -> None:
    with pytest.raises(ProtocolError, match="device reports"):
        parse_tactile_descriptor("Fn_U8_70", total_data_length=72)


@pytest.mark.parametrize(
    "descriptor",
    ("", "Fn_U8", "Fn_U8_0", "Fn_X8_1", "Zz_U8_1", "Fn_U8_07"),
)
def test_parse_descriptor_rejects_malformed_fields(descriptor: str) -> None:
    with pytest.raises(ProtocolError):
        parse_tactile_descriptor(descriptor)


def test_decode_fields_round_trips_little_endian_values() -> None:
    fields = parse_tactile_descriptor("FnS_U16_1;Fn_U8_70", total_data_length=72)
    normal_force = bytes(range(70))
    raw = struct.pack("<H", 4097) + normal_force

    resultant, normal = decode_tactile_fields(fields, raw)

    assert resultant.values == (4097,)
    assert normal.values == tuple(range(70))


def test_decode_fields_groups_xyz_triples() -> None:
    fields = parse_tactile_descriptor("xyz_I16_2")
    raw = struct.pack("<6h", -1, 0, 1, 2, 3, 4)

    (field,) = decode_tactile_fields(fields, raw)

    assert field.values == ((-1, 0, 1), (2, 3, 4))


def test_decode_fields_rejects_wrong_length() -> None:
    fields = parse_tactile_descriptor("Fn_U8_4")
    with pytest.raises(ProtocolError, match="descriptor requires"):
        decode_tactile_fields(fields, b"\x00\x01")


def test_sensor_chunks_split_the_72_byte_stream_within_channel_one() -> None:
    assert sensor_data_chunks(72) == (
        (MI_SENSOR_DATA_1, 0, 61),
        (MI_SENSOR_DATA_1, 61, 11),
    )


def test_sensor_chunks_span_channels_two_and_three_for_large_streams() -> None:
    chunks = sensor_data_chunks(400)

    channels = {channel for channel, _sub_index, _length in chunks}
    assert channels == {MI_SENSOR_DATA_1, MI_SENSOR_DATA_2}
    assert sum(length for _channel, _sub_index, length in chunks) == 400
    # Every channel-two fragment addresses global byte 255 onward.
    assert all(
        sub_index + length <= 255
        for channel, sub_index, length in chunks
        if channel == MI_SENSOR_DATA_2
    )

    wide = sensor_data_chunks(765)
    assert {channel for channel, _s, _l in wide} == {
        MI_SENSOR_DATA_1,
        MI_SENSOR_DATA_2,
        MI_SENSOR_DATA_3,
    }
    assert sum(length for _c, _s, length in wide) == 765


@pytest.mark.parametrize("length", (0, 766, 767, 768))
def test_sensor_chunks_reject_out_of_range_lengths(length: int) -> None:
    with pytest.raises(ProtocolError):
        sensor_data_chunks(length)
