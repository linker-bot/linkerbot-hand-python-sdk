"""Partial tactile availability must not erase remaining fingertip data."""

from dataclasses import replace

import pytest

from linkerbot_hand import (
    HandDevice,
    NormalForceResultantSample,
    ProtocolError,
    TelemetrySemantic,
    UnsupportedFirmwareError,
    UnsupportedOperationError,
)

from .o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
    _no_sensor_finger,
)


class MixedSensors(O30DeviceScript):
    absent = frozenset({2, 4})
    descriptor = None

    def _sensor_finger(self, selected_sensor):
        if selected_sensor in self.absent:
            return _no_sensor_finger(selected_sensor)
        finger = super()._sensor_finger(selected_sensor)
        if self.descriptor is not None:
            sizes = {"U8": 1, "U16": 2}
            size = sum(
                int(item.split("_")[2]) * sizes[item.split("_")[1]]
                for item in self.descriptor.split(";")
            )
            finger = replace(
                finger,
                descriptor_text=self.descriptor,
                data=bytes(index % 251 for index in range(size)),
            )
        return replace(finger, sensor_type="触觉", data_unit="克")

    def _read(self, main_index, sub_index, length, *, rts):
        if main_index == 0x31 and sub_index == 0x2C and length > 61:
            source = self._sensor_object(self.selected_sensor)
            return tuple(
                self._response(
                    0x31,
                    sub_index + offset,
                    source[sub_index + offset : sub_index + min(length, offset + 61)],
                )
                for offset in range(0, length, 61)
            )
        return super()._read(main_index, sub_index, length, rts=rts)


def make_hand(script):
    return HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))


def test_partial_sensor_absence_preserves_identities_and_reuses_metadata():
    script = MixedSensors(sensor_variant="TSSP_JZG")
    hand = make_hand(script)
    hand.open()
    try:
        sample = hand.get_force(timeout=1)
        assert len(sample.sensors) == 5
        for index, sensor in enumerate(sample.sensors, start=1):
            assert sensor.metadata.selected_sensor == index
            assert sensor.metadata.available == (index not in script.absent)
            if index in script.absent:
                assert sensor.raw_data == b"" and sensor.fields == ()
            else:
                assert sensor.metadata.sensor_type == "触觉"
                assert sensor.metadata.data_unit == "克"
                assert sensor.fields[0].values == (1000 + index,)
        with pytest.raises(UnsupportedOperationError, match="index"):
            NormalForceResultantSample.from_force_sample(sample)
        count = len(script.sent_messages)
        assert hand.get_force(timeout=1).meta.sequence > sample.meta.sequence
        warm_requests = script.sent_messages[count:]
        assert not any(
            msg.data[0] == 0x31 and len(msg.data) == 3 for msg in warm_requests
        )
        selected = [
            msg.data[3] for msg in warm_requests if msg.data[:3] == b"\x31\x6e\x01"
        ]
        assert selected == [1, 3, 5]
        assert hand.get_capabilities().telemetry_supported(TelemetrySemantic.FORCE)
    finally:
        hand.close()


@pytest.mark.parametrize(
    "descriptor",
    [
        "Fn_U8_1;" * 7 + "x_U8_1",
        "Fn_U8_1;" * 7 + "Fn_U8_1",
        "Fn_U8_1;" * 7 + "Fn_U16_1",
    ],
)
def test_62_to_64_byte_descriptors_complete_through_real_fragment_assembly(descriptor):
    assert 62 <= len(descriptor) <= 64
    script = MixedSensors(sensor_variant="TSSP_JZG")
    script.descriptor = descriptor
    hand = make_hand(script)
    hand.open()
    try:
        sample = hand.get_force(timeout=1)
        assert sample.sensors[0].metadata.descriptor_text == descriptor
        assert len(sample.sensors[0].fields) == 8
    finally:
        hand.close()


def test_three_pages_reassemble_into_exact_original_bytes():
    script = MixedSensors(sensor_variant="TSSP_JZG")
    script.descriptor = "Fn_U8_765"
    hand = make_hand(script)
    hand.open()
    try:
        sample = hand.get_force(timeout=2)
        assert sample.sensors[0].raw_data == bytes(index % 251 for index in range(765))
        assert sample.sensors[0].fields[0].values == tuple(
            index % 251 for index in range(765)
        )
    finally:
        hand.close()


def test_all_absent_is_unsupported_without_fabricating_a_force_sample():
    script = MixedSensors(sensor_variant="TSSP_JZG")
    script.absent = {1, 2, 3, 4, 5}
    hand = make_hand(script)
    hand.open()
    try:
        with pytest.raises(UnsupportedFirmwareError, match="does not support force"):
            hand.get_force(timeout=1)
        assert hand.get_health().connected
        assert not hand.get_capabilities().telemetry_supported(TelemetrySemantic.FORCE)
    finally:
        hand.close()


def test_installed_sensor_with_missing_descriptor_is_not_treated_as_absent():
    class CorruptSensor(MixedSensors):
        def _sensor_object(self, selected_sensor):
            data = bytearray(super()._sensor_object(selected_sensor))
            if selected_sensor == 1:
                data[0x2B] = 0
                data[0x6C:0x6E] = b"\x00\x00"
            return bytes(data)

    hand = make_hand(CorruptSensor(sensor_variant="TSSP_JZG"))
    hand.open()
    try:
        with pytest.raises(ProtocolError, match="no descriptor"):
            hand.get_force(timeout=1)
    finally:
        hand.close()
