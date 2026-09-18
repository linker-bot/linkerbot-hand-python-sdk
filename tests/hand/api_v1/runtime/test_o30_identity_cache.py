"""Identity refresh and connection changes have different cache lifetimes."""

import pytest

from linkerbot_hand import HandDevice, HandSide, IdentityMismatchError
from linkerbot_hand.protocols.o30 import O30ProtocolBackend

from .classic_can_fake import wait_until
from .o30_canfd_fake import O30DeviceScript, ScriptedO30Transport


def metadata_reads(script):
    return sum(
        msg.data[0] == 0x31 and len(msg.data) == 3 for msg in script.sent_messages
    )


@pytest.mark.parametrize("layout", ["deployed", "documented"])
def test_same_identity_refresh_keeps_cache_but_recovery_reloads_it(layout):
    script = O30DeviceScript(sensor_variant="TSSP_JZG", identity_layout=layout)
    transport = ScriptedO30Transport(script)
    hand = HandDevice(model="O30", side="right", transport=transport)
    hand.open()
    try:
        first = hand.get_force(timeout=1)
        initial_reads = metadata_reads(script)
        assert initial_reads > 0
        hand.get_device_info(timeout=1)
        hand.get_force(timeout=1)
        assert metadata_reads(script) == initial_reads
        transport.current_dispatcher.disconnect()
        wait_until(lambda: not hand.get_health().connected)
        hand.recover(timeout=1)
        refreshed = hand.get_force(timeout=1)
        assert refreshed.meta.connection_generation > first.meta.connection_generation
        assert metadata_reads(script) == 2 * initial_reads
    finally:
        hand.close()


def test_changed_identity_invalidates_cached_sensor_metadata():
    script = O30DeviceScript(sensor_variant="TSSP_JZG")
    backend = O30ProtocolBackend(HandSide.RIGHT)
    hand = HandDevice(
        model="O30",
        side="right",
        transport=ScriptedO30Transport(script),
        protocol_backend=backend,
    )
    hand.open()
    try:
        hand.get_force(timeout=1)
        assert backend._sensor_metadata
        script.application_version = "1.3.0"
        with pytest.raises(IdentityMismatchError):
            hand.get_device_info(timeout=1)
        assert not backend._sensor_metadata
    finally:
        hand.close()


@pytest.mark.parametrize("layout", ["deployed", "documented"])
def test_identity_reads_each_wire_byte_once_and_keeps_utf8(layout):
    class TextIdentity(O30DeviceScript):
        def _identity_object(self, main_index):
            source = super()._identity_object(main_index)
            if main_index != 0x41 or source is None:
                return source
            data = bytearray(source)
            data[0x10:0x20] = "十二伏".encode().ljust(16, b"\x00")
            if self.identity_layout == "documented":
                data[0x69:0x79] = "接口板版本".encode().ljust(16, b"\x00")
            return bytes(data)

    script = TextIdentity(identity_layout=layout, protocol_version="9.9.9")
    hand = HandDevice(model="O30", side="right", transport=ScriptedO30Transport(script))
    hand.open()
    try:
        info = hand.get_snapshot().device_info
        assert info.power_supply_range == "十二伏"
        assert info.protocol_version == "9.9.9"
        if layout == "documented":
            assert info.pcb_version.text == "接口板版本"
        covered = []
        for msg in script.sent_messages:
            if msg.data[0] == 0x41:
                covered.extend(range(msg.data[1], msg.data[1] + msg.data[2]))
        assert len(covered) == len(set(covered))
        assert set(covered) == set(range(225 if layout == "deployed" else 249))
    finally:
        hand.close()
