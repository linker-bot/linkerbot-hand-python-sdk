"""Protocol page boundaries and reserved field offsets, independent of fakes."""

import pytest

from linkerbot_hand.protocols.o30.sensors import (
    decode_tactile_fields,
    parse_tactile_descriptor,
    sensor_data_chunks,
)


@pytest.mark.parametrize("length", (254, 255, 256, 509, 510, 511, 764, 765))
def test_pages_have_exactly_255_bytes(length):
    expected_offset = 0
    for mi, si, size in sensor_data_chunks(length):
        assert mi in (0x32, 0x33, 0x34)
        assert (mi - 0x32) * 255 + si == expected_offset
        assert 1 <= size <= 61
        assert si + size <= 255
        expected_offset += size
    assert expected_offset == length


def test_reserved_descriptor_keeps_value_and_offset_without_becoming_force():
    fields = parse_tactile_descriptor(
        "FnS_U16_1;FtS_U16_1;FdS_U16_1;Rsv_U16_1;Fn_U8_40",
        total_data_length=48,
    )
    raw = b"\x01\x00\x02\x00\x03\x00\x34\x12" + bytes(range(40))
    decoded = decode_tactile_fields(fields, raw)
    assert decoded[3].semantic == "Rsv"
    assert decoded[3].values == (0x1234,)
    assert decoded[4].byte_offset == 8
    assert decoded[4].values == tuple(range(40))
