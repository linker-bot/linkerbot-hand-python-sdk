"""Captured scalar tactile streams must share the existing O30 force API."""

from contextlib import closing
from dataclasses import replace

import pytest

from linkerbot_hand import (
    HandDevice,
    NormalForceResultantSample,
    UnsupportedOperationError,
)

from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


class PaddedTashanScript(O30DeviceScript):
    def _response(self, main_index, sub_index, payload, *, rts=False):
        response = super()._response(main_index, sub_index, payload, rts=rts)
        if main_index == 0x32:
            # Real 14-byte data has a three-byte HOP header and CAN FD padding.
            response = replace(response, data=response.data.ljust(20, b"\x00"))
        return response


@pytest.mark.parametrize("fields", [(), ("fn",), ("fn", "ft", "fd", "px")])
def test_tashan_selectors_return_all_captured_fields_in_one_five_finger_read(fields):
    script = PaddedTashanScript(sensor_variant="TASHAN_GATHER")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    assert hand.get_capabilities().force_field("fns").supported is None
    with closing(hand.open()):
        first = hand.get_force(*fields, timeout=1)
        expected = {
            "fn": (
                0.0,
                6.18657112121582,
                7.829746246337891,
                6.939646244049072,
                4.243380546569824,
            ),
            "ft": (
                0.0,
                0.17716196179389954,
                0.44500094652175903,
                0.2557319402694702,
                0.15781886875629425,
            ),
            "fd": (0, 281, 301, 357, 86),
            "px": (0, 1265862, 1403507, 1448689, 1199708),
        }
        assert tuple(field.name for field in first.fields) == tuple(expected)
        assert first.raw == first.tactile == ()
        for name, values in expected.items():
            field = first.field(name)
            assert field.raw == tuple((value,) for value in values)
            assert field.meta is first.meta
            assert field.units == (("N",) * 5 if name in {"fn", "ft"} else (None,) * 5)
            capability = hand.get_capabilities().force_field(name)
            assert capability.supported is True
            assert capability.shared_fields == tuple(expected)
            assert capability.sensor_names == (
                "thumb",
                "index",
                "middle",
                "ring",
                "pinky",
            )
        assert first.fn.encodings == first.ft.encodings == ("F32",) * 5
        assert first.fd.encodings == ("U16",) * 5
        assert first.px.encodings == ("U32",) * 5
        for sensor in first.sensors:
            metadata = sensor.metadata
            assert metadata.sensor_type == "TASHAN_GATHER"
            assert metadata.available
            assert metadata.maximum_rows == metadata.maximum_columns == 0
            assert metadata.requested_rows == metadata.requested_columns == 0
            assert not metadata.rectangle_fill
            assert metadata.descriptor_length == 35
            assert metadata.total_data_length == len(sensor.raw_data) == 14
            assert tuple(field.byte_offset for field in sensor.fields) == (0, 4, 8, 10)
        assert first.sensors[0].raw_data == bytes(14)
        assert first.fn.raw[0] == (0.0,)
        assert first.fn.valid_values[0] == (True,)
        assert first.status("fn") == "available"

        sent = len(script.sent_messages)
        second = hand.get_force(*fields, timeout=1)
        assert second.meta.sequence > first.meta.sequence
        assert [message.data for message in script.sent_messages[sent:]] == [
            request
            for sensor_id in range(1, 6)
            for request in (bytes((0x31, 0x6E, 1, sensor_id)), b"\x32\x00\x0e")
        ]
        assert tuple(field.name for field in second.fields) == tuple(expected)
        assert [sensor.raw_data for sensor in second.sensors] == [
            sensor.raw_data for sensor in first.sensors
        ]

        sent = len(script.sent_messages)
        for name, values in expected.items():
            assert second.field(name).raw == tuple((value,) for value in values)
            assert hand.get_snapshot().force.field(name) is not None
            assert hand.get_capabilities().force_field(name).supported
        assert len(script.sent_messages) == sent


def test_tashan_missing_resultants_remain_unsupported_without_erasing_fn():
    script = PaddedTashanScript(sensor_variant="TASHAN_GATHER")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        force = hand.get_force(timeout=1)
        sent = len(script.sent_messages)
        for name in ("fns", "fts", "fds"):
            assert force.field(name) is None
            assert force.status(name) == "unsupported"
            assert hand.get_capabilities().force_field(name).supported is False
            with pytest.raises(UnsupportedOperationError, match=name):
                hand.get_force(name, timeout=1)
        with pytest.raises(UnsupportedOperationError, match="FnS"):
            NormalForceResultantSample.from_force_sample(force)
        assert len(script.sent_messages) == sent
        assert force.fn.meta.valid
        assert force.fn.raw[1] == (6.18657112121582,)


def test_tashan_separate_sequential_getters_each_read_a_new_native_stream():
    script = PaddedTashanScript(sensor_variant="TASHAN_GATHER")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        hand.get_force(timeout=1)
        sent = len(script.sent_messages)
        samples = [hand.get_force(name, timeout=1) for name in ("fn", "ft", "fd", "px")]
        assert len({sample.meta.sequence for sample in samples}) == 4
        requests = script.sent_messages[sent:]
        assert len(requests) == 40
        assert sum(message.data == b"\x32\x00\x0e" for message in requests) == 20


def test_tashan_observed_direction_special_value_is_preserved_without_conversion():
    class CurrentTashanScript(PaddedTashanScript):
        def _sensor_finger(self, selected_sensor):
            finger = super()._sensor_finger(selected_sensor)
            if selected_sensor == 2:
                # Also captured on 2026-09-29. Its direction meaning is unconfirmed.
                return replace(
                    finger, data=bytes.fromhex("0000000000000000ffff00000000")
                )
            return finger

    script = CurrentTashanScript(sensor_variant="TASHAN_GATHER")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    with closing(hand.open()):
        sample = hand.get_force(timeout=1)
        assert sample.meta.valid and sample.sensors[1].metadata.available
        assert sample.fn.raw[1] == sample.ft.raw[1] == (0.0,)
        assert sample.fd.raw[1] == (65535,)
        assert sample.fd.units[1] is None
        assert sample.sensors[1].raw_data == bytes.fromhex(
            "0000000000000000ffff00000000"
        )
        # Deliberately make no assertion about physical direction validity.
