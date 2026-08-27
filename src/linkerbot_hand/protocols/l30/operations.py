"""ACK-gated L30 enable, disable, and zero-calibration operations."""

from __future__ import annotations

from ...api.errors import UnsupportedOperationError
from ...capabilities.types import DeviceCapabilities
from ..base import ProtocolTransaction, ResponseProgress
from .codec import (
    CONFIG_UNLOCK_PASSWORD,
    PARENT_CONFIG,
    PARENT_CONTROL,
    SUB_CALIBRATE,
    SUB_CONFIG_UNLOCK,
    SUB_DISABLE,
    SUB_ENABLE,
    decode_ack,
)
from .framing import build_request, matching_response_data


def operation_transaction(
    operation: str,
    capabilities: DeviceCapabilities,
    *,
    node_id: int,
    host_id: int,
) -> ProtocolTransaction:
    """Build one supported operation without exposing its internal stages."""

    if not getattr(capabilities.operations, operation, False):
        raise UnsupportedOperationError(f"L30 does not support {operation}")
    if operation == "enable":
        return _ack_transaction(
            operation,
            parent=PARENT_CONTROL,
            subcommand=SUB_ENABLE,
            node_id=node_id,
            host_id=host_id,
        )
    if operation == "disable":
        return _ack_transaction(
            operation,
            parent=PARENT_CONTROL,
            subcommand=SUB_DISABLE,
            node_id=node_id,
            host_id=host_id,
        )
    if operation != "calibrate":
        raise UnsupportedOperationError(f"L30 does not support {operation}")
    calibrate = _ack_transaction(
        "calibrate",
        parent=PARENT_CONFIG,
        subcommand=SUB_CALIBRATE,
        node_id=node_id,
        host_id=host_id,
    )
    unlock = _ack_transaction(
        "calibrate_unlock",
        parent=PARENT_CONFIG,
        subcommand=SUB_CONFIG_UNLOCK,
        node_id=node_id,
        host_id=host_id,
        payload=CONFIG_UNLOCK_PASSWORD,
        dlc=0x09,
        next_transaction=calibrate,
    )
    return _ack_transaction(
        "calibrate_disable",
        parent=PARENT_CONTROL,
        subcommand=SUB_DISABLE,
        node_id=node_id,
        host_id=host_id,
        next_transaction=unlock,
    )


def _ack_transaction(
    key: str,
    *,
    parent: int,
    subcommand: int,
    node_id: int,
    host_id: int,
    payload: bytes = b"",
    dlc: int | None = None,
    next_transaction: ProtocolTransaction | None = None,
) -> ProtocolTransaction:
    def consume(message: object) -> ResponseProgress:
        data = matching_response_data(
            message,
            parent=parent,
            subcommand=subcommand,
            write=True,
            node_id=node_id,
            host_id=host_id,
        )
        if data is None:
            return ResponseProgress(matched=False)
        decode_ack(data)
        return ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
        )

    return ProtocolTransaction(
        key=("operation", key),
        correlation_key=("l30_write", node_id, parent, subcommand),
        request_messages=(
            build_request(
                parent=parent,
                subcommand=subcommand,
                write=True,
                payload=payload,
                dlc=dlc,
                node_id=node_id,
                host_id=host_id,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.004,
        response_consumer=consume,
        late_response_guard_s=0.01,
        next_transaction=next_transaction,
    )


__all__ = ["operation_transaction"]
