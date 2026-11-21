"""Classic CAN adapter for the shared O30 object-model test fixture."""

from __future__ import annotations

import can

from linkerbot_transport import CANRouteKey, SendReceipt
from linkerbot_transport.canfd import CANFDMessage
from linkerbot_transport.testing import FakeTransport

from .o30_canfd_fake import _WRITABLE_OBJECTS, O30DeviceScript


class ClassicO30Transport(FakeTransport[can.Message]):
    """Split replies into five-byte payloads and apply range writes in place."""

    def __init__(
        self, script: O30DeviceScript, *, drop_write_si=None, error_write_si=None
    ):
        super().__init__(
            route_key_of=lambda m: CANRouteKey(m.arbitration_id, m.is_extended_id)
        )
        self.script = script
        self.requests: list[can.Message] = []
        self.drop_write_si = drop_write_si
        self.error_write_si = error_write_si

    def send(self, message: can.Message) -> SendReceipt:
        assert isinstance(message, can.Message)
        assert not message.is_fd and not message.bitrate_switch
        assert len(message.data) <= 8
        receipt = super().send(message)
        if receipt.exception() is not None:
            return receipt
        self.requests.append(message)
        data = bytes(message.data)
        if message.arbitration_id != self.script.request_id or len(data) < 3:
            return receipt
        mi, si, count = data[:3]
        if len(data) > 3 and mi & 0x7F in _WRITABLE_OBJECTS:
            if si == self.drop_write_si:
                return receipt
            if si == self.error_write_si:
                replies = (self.script._error(si, 0x10),)
            else:
                target = bytearray(self.script.targets[mi & 0x7F])
                target[si : si + count] = data[3:]
                replies = self.script.respond(
                    CANFDMessage(
                        arbitration_id=message.arbitration_id,
                        data=bytes((mi, 0, 25)) + bytes(target),
                        is_extended_id=False,
                        frame_type=4,
                    )
                )
                replies = tuple(
                    CANFDMessage(
                        arbitration_id=r.arbitration_id,
                        data=bytes((mi, si, count)) + r.data[3 + si : 3 + si + count],
                        is_extended_id=False,
                        frame_type=4,
                    )
                    for r in replies
                )
        else:
            replies = self.script.respond(
                CANFDMessage(
                    arbitration_id=message.arbitration_id,
                    data=data,
                    is_extended_id=False,
                    frame_type=4,
                )
            )
        for reply in replies:
            control, start, size = reply.data[:3]
            payload = reply.data[3 : 3 + size]
            for offset in range(0, len(payload), 5):
                chunk = payload[offset : offset + 5]
                self.emit(
                    can.Message(
                        arbitration_id=reply.arbitration_id,
                        data=bytes((control, start + offset, len(chunk))) + chunk,
                        is_extended_id=False,
                    )
                )
        return receipt
