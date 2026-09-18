"""Protocol-derived HOP ranges and communication flags, independent of fakes."""

import pytest

from linkerbot_hand import ProtocolError, UnsupportedFirmwareError
from linkerbot_hand.protocols.o30.response import HOPRangeAssembler
from linkerbot_transport.canfd import CANFDMessage


def response(mi, si, payload):
    return CANFDMessage(
        arbitration_id=0x401,
        is_extended_id=False,
        frame_type=4,
        data=bytes((mi, si, len(payload))) + payload,
    )


def assembler(*, optional=False):
    return HOPRangeAssembler(
        0x32,
        sub_index=10,
        length=10,
        rts=False,
        response_id=0x401,
        object_unavailable_is_unsupported=optional,
    )


@pytest.mark.parametrize(
    ("code", "texts"),
    [
        (0x03, ("main index", "sub-index")),
        (0x1C, ("not writable", "length mismatch", "permission denied")),
        (0x20, ("zero padding",)),
        (0xC4, ("not writable", "unknown flags 0xC0")),
    ],
)
def test_communication_flags_are_combined_without_mechanical_faults(code, texts):
    with pytest.raises(ProtocolError) as exc:
        assembler().consume(response(0x4F, 1, bytes((code,))))
    assert f"0x{code:02X}" in str(exc.value)
    assert all(text in str(exc.value) for text in texts)


@pytest.mark.parametrize("code", (0x01, 0x02, 0x03, 0x21, 0x23))
def test_only_explicit_missing_optional_objects_disable_the_channel(code):
    with pytest.raises(UnsupportedFirmwareError):
        assembler(optional=True).consume(response(0x4F, 1, bytes((code,))))
    with pytest.raises(ProtocolError):
        assembler(optional=True).consume(response(0x4F, 1, bytes((code | 8,))))


def test_zero_error_history_does_not_complete_a_data_request():
    assert assembler().consume(response(0x4F, 1, b"\x00")) == (False, None)


@pytest.mark.parametrize(("si", "length"), [(8, 3), (19, 2), (0, 21)])
def test_overlapping_out_of_bounds_range_is_rejected(si, length):
    with pytest.raises(ProtocolError, match="overlaps"):
        assembler().consume(response(0x32, si, bytes(length)))


def test_unrelated_ranges_remain_ignored_and_cannot_complete_request():
    value = assembler()
    assert value.consume(response(0x32, 0, bytes(10))) == (False, None)
    assert value.consume(response(0x32, 20, bytes(10))) == (False, None)
    assert value.consume(response(0x32, 10, bytes(10))) == (True, bytes(10))
