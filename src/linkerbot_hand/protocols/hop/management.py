"""HOP 0x35 configuration reads and single-frame reconfiguration writes."""

from ...api.can_configuration import DATA_BITRATES, NOMINAL_BITRATES, CANConfiguration
from ...api.errors import ProtocolError
from ..base import ProtocolTransaction, ResponseProgress
from .framing import HOPAddressing, build_read_message, build_write_message
from .response import HOPRangeAssembler


def configuration_transaction(addressing: HOPAddressing) -> ProtocolTransaction:
    values: dict[int, bytes] = {}
    spans = ((0, 2), (6, 4))

    def stage(index: int) -> ProtocolTransaction:
        start, count = spans[index]
        assembler = HOPRangeAssembler(
            0x35,
            sub_index=start,
            length=count,
            rts=True,
            response_id=addressing.response_id,
        )

        def consume(message: object) -> ResponseProgress:
            matched, complete = assembler.consume(message)
            if complete is None:
                return ResponseProgress(matched=matched)
            values[start] = complete
            result = None
            if index == 1:
                interface, brs, nominal, data = complete
                if (
                    interface not in (1, 2)
                    or brs not in (0, 1)
                    or not 1 <= nominal <= len(NOMINAL_BITRATES)
                    or not 1 <= data <= len(DATA_BITRATES)
                ):
                    raise ProtocolError("HOP returned invalid CAN configuration codes")
                request_id = int.from_bytes(values[0], "little")
                if not 0 <= request_id <= 0x3FE:
                    raise ProtocolError("HOP returned invalid standard CAN ID")
                result = CANConfiguration(
                    request_id,
                    interface,
                    bool(brs),
                    NOMINAL_BITRATES[nominal - 1],
                    DATA_BITRATES[data - 1],
                )
            return ResponseProgress(matched=True, complete=True, result=result)

        return ProtocolTransaction(
            key=("hop_can_configuration", index),
            correlation_key=("hop", addressing.response_id),
            request_messages=(
                build_read_message(
                    0x35,
                    sub_index=start,
                    length=count,
                    rts=True,
                    request_id=addressing.request_id,
                    brs=addressing.brs,
                    can_fd=addressing.can_fd,
                ),
            ),
            response_timeout_s=0.1,
            estimated_duration_s=0.003,
            response_consumer=consume,
            late_response_guard_s=0.01,
            next_transaction_factory=(lambda: stage(1)) if index == 0 else None,
        )

    return stage(0)


def configuration_write(
    addressing: HOPAddressing, sub_index: int, payload: bytes
) -> ProtocolTransaction:
    """Dispatch only: applying a new route can prevent an old-route ACK."""
    valid = (
        (
            sub_index == 0
            and len(payload) == 2
            and int.from_bytes(payload, "little") <= 0x3FE
        )
        or (sub_index == 7 and payload in (b"\x00", b"\x01"))
        or (
            sub_index == 8
            and len(payload) in (1, 2)
            and 1 <= payload[0] <= len(NOMINAL_BITRATES)
            and (len(payload) == 1 or 1 <= payload[1] <= len(DATA_BITRATES))
        )
        or (
            sub_index == 9
            and len(payload) == 1
            and 1 <= payload[0] <= len(DATA_BITRATES)
        )
    )
    if not valid:
        raise ProtocolError("invalid HOP CAN configuration write")
    return ProtocolTransaction(
        key=("hop_can_write", sub_index),
        correlation_key=("hop", addressing.response_id),
        request_messages=(
            build_write_message(
                0x35,
                sub_index=sub_index,
                payload=payload,
                rts=True,
                request_id=addressing.request_id,
                brs=addressing.brs,
                can_fd=addressing.can_fd,
            ),
        ),
        response_timeout_s=0,
        estimated_duration_s=0.003,
    )


def save_transaction(addressing: HOPAddressing) -> ProtocolTransaction:
    assembler = HOPRangeAssembler(
        0x35, sub_index=0x1E, length=1, rts=True, response_id=addressing.response_id
    )

    def consume(message: object) -> ResponseProgress:
        matched, data = assembler.consume(message)
        return ResponseProgress(
            matched=matched, complete=data is not None, acknowledged=data is not None
        )

    return ProtocolTransaction(
        key=("hop_save",),
        correlation_key=("hop", addressing.response_id),
        request_messages=(
            build_write_message(
                0x35,
                sub_index=0x1E,
                payload=b"\x01",
                rts=True,
                request_id=addressing.request_id,
                brs=addressing.brs,
                can_fd=addressing.can_fd,
            ),
        ),
        response_timeout_s=0.2,
        estimated_duration_s=0.005,
        response_consumer=consume,
        response_acknowledges_command=True,
        late_response_guard_s=0.01,
    )
