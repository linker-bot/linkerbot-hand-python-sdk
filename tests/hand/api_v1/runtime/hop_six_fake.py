"""Six-joint HOP script with independent actual/configured register contents."""

from linkerbot_hand import HandSide
from tests.hand.api_v1.runtime.o30_canfd_fake import O30DeviceScript


class HOPSixScript(O30DeviceScript):
    def __init__(
        self, model="L6", side=HandSide.RIGHT, request_id=1, sensor_variant="NO_SENSOR"
    ):
        super().__init__(
            product_model=model,
            device_uid=f"UID-{model}",
            identity_layout="documented",
            reported_side=side,
            request_id=request_id,
            response_id=request_id | 0x400,
            sensor_variant=sensor_variant,
        )
        for mi in range(1, 13):
            self.actual[mi] = bytes(range(36)) if mi != 12 else bytes(36)
            self.targets[mi] = bytes(range(100, 136))
        for mi in (0x21, 0x23):
            self.actual[mi] = self.targets[mi] = b"".join(
                n.to_bytes(2, "little") for n in range(36)
            )
        self.configuration = bytearray(32)
        self.configuration[:2] = request_id.to_bytes(2, "little")
        self.configuration[6:10] = bytes([2, 0, 1, 5])
        self.drop_range = None
        self.reject_range = None
        self.drop_configuration_ack = True

    def _identity_object(self, main_index):
        if main_index == 0x35:
            return bytes(self.configuration)
        return super()._identity_object(main_index)

    def _read(self, main_index, sub_index, length, *, rts):
        if (main_index, sub_index) == self.drop_range:
            return ()
        if (main_index, sub_index) == self.reject_range:
            return (self._error(sub_index, 2),)
        return super()._read(main_index, sub_index, length, rts=rts)

    def _write(self, main_index, sub_index, length, payload, *, rts=False):
        if main_index == 0x31:
            return super()._write(main_index, sub_index, length, payload, rts=rts)
        if main_index == 0x35:
            self.configuration[sub_index : sub_index + length] = payload[:length]
            if sub_index == 0:
                self.request_id = int.from_bytes(payload[:2], "little")
                self.response_id = self.request_id | 0x400
            if sub_index != 0x1E and self.drop_configuration_ack:
                return ()
            return (self._response(main_index, sub_index, payload[:length], rts=rts),)
        if main_index not in self.targets:
            return (self._error(sub_index, 2),)
        width = 2 if main_index in (0x21, 0x23) else 1
        if (sub_index, length) not in ((5 * width, width), (10 * width, 5 * width)):
            return (self._error(sub_index, 0x40),)
        if (main_index, sub_index) == self.drop_range:
            return ()
        if (main_index, sub_index) == self.reject_range:
            return (self._error(sub_index, 2),)
        target = bytearray(self.targets[main_index])
        target[sub_index : sub_index + length] = payload[:length]
        self.targets[main_index] = bytes(target)
        if main_index == 1:
            self.actual[main_index] = bytes(target)
        data = (self.targets if rts else self.actual)[main_index]
        return (
            self._response(
                main_index, sub_index, data[sub_index : sub_index + length], rts=rts
            ),
        )
