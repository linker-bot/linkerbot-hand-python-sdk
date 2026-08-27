"""Response-gated L30 five-finger tactile transaction."""

from __future__ import annotations

from ...api.enums import DataOrigin
from ...api.errors import ProtocolError, UnsupportedOperationError
from ...api.readings import TactileMatrix
from ...capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..base import ProtocolTransaction, ResponseProgress, SamplePayload
from .codec import (
    PARENT_TACTILE,
    TACTILE_COLUMNS,
    TACTILE_LENGTH,
    TACTILE_ROWS,
    decode_tactile_fragment,
)
from .framing import build_request, matching_response_data

_FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def tactile_transaction(
    capabilities: DeviceCapabilities,
    *,
    node_id: int,
    host_id: int,
) -> ProtocolTransaction:
    """Query each finger only after both frames of the prior finger arrive."""

    telemetry = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    if telemetry is None or not telemetry.device_queried:
        raise UnsupportedOperationError("L30 has no active tactile query")
    matrices: dict[int, TactileMatrix] = {}
    next_transaction: ProtocolTransaction | None = None
    for finger_index in reversed(range(len(_FINGERS))):
        next_transaction = _finger_transaction(
            finger_index,
            matrices,
            node_id=node_id,
            host_id=host_id,
            estimated_duration_s=telemetry.estimated_transaction_s / len(_FINGERS),
            next_transaction=next_transaction,
        )
    if next_transaction is None:
        raise ProtocolError("L30 tactile profile has no fingers")
    return next_transaction


def _finger_transaction(
    finger_index: int,
    matrices: dict[int, TactileMatrix],
    *,
    node_id: int,
    host_id: int,
    estimated_duration_s: float,
    next_transaction: ProtocolTransaction | None,
) -> ProtocolTransaction:
    subcommand = finger_index + 1
    fragments: dict[int, bytes] = {}

    def consume(message: object) -> ResponseProgress:
        data = matching_response_data(
            message,
            parent=PARENT_TACTILE,
            subcommand=subcommand,
            write=False,
            node_id=node_id,
            host_id=host_id,
        )
        if data is None:
            return ResponseProgress(matched=False)
        sequence, fragment = decode_tactile_fragment(data)
        previous = fragments.get(sequence)
        if previous is not None and previous != fragment:
            raise ProtocolError("L30 tactile fragment conflicts with a duplicate")
        fragments[sequence] = fragment
        if len(fragments) != 2:
            return ResponseProgress(matched=True)
        raw = fragments[0] + fragments[1]
        if len(raw) != TACTILE_LENGTH:
            raise ProtocolError("L30 tactile matrix length is not 72 bytes")
        matrices[finger_index] = _matrix(_FINGERS[finger_index], raw)
        result = None
        if next_transaction is None:
            try:
                ordered = tuple(matrices[index] for index in range(len(_FINGERS)))
            except KeyError as error:
                raise ProtocolError(
                    "L30 tactile transaction missed a finger"
                ) from error
            result = SamplePayload(
                semantic=TelemetrySemantic.FORCE,
                tactile=ordered,
                origin=DataOrigin.DEVICE_RESPONSE,
            )
        return ResponseProgress(matched=True, complete=True, result=result)

    return ProtocolTransaction(
        key=("read", TelemetrySemantic.FORCE, subcommand),
        correlation_key=("l30_tactile", node_id, subcommand),
        request_messages=(
            build_request(
                parent=PARENT_TACTILE,
                subcommand=subcommand,
                write=False,
                node_id=node_id,
                host_id=host_id,
            ),
        ),
        response_timeout_s=0.05,
        estimated_duration_s=estimated_duration_s,
        response_consumer=consume,
        late_response_guard_s=0.02,
        next_transaction=next_transaction,
    )


def _matrix(name: str, raw: bytes) -> TactileMatrix:
    rows = tuple(
        tuple(raw[offset : offset + TACTILE_COLUMNS])
        for offset in range(0, len(raw), TACTILE_COLUMNS)
    )
    return TactileMatrix(
        name=name,
        rows=TACTILE_ROWS,
        columns=TACTILE_COLUMNS,
        raw=rows,
        online=True,
    )


__all__ = ["tactile_transaction"]
