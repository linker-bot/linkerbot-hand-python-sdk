"""L30 DeviceInfo and product-code identity transaction."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...api.device_info import DeviceInfo, VersionField
from ...api.enums import HandSide
from ...api.errors import IdentityMismatchError, ProtocolError
from ...api.readings import SampleMeta
from ...capabilities.types import DeviceCapabilities
from ..base import IdentityResult, ProtocolTransaction, ResponseProgress
from .codec import (
    DEVICE_INFO_LENGTH,
    PARENT_CONFIG,
    SUB_DEVICE_INFO,
    SUB_PRODUCT_CODE,
    decode_ascii_response,
    decode_response_payload,
)
from .framing import build_request, matching_response_data

_PRODUCT_ID = 0x13
_LEFT_CODE = 0
_RIGHT_CODE = 1
_SIDE_LABELS = {_LEFT_CODE: "L", _RIGHT_CODE: "R"}


@dataclass(frozen=True, slots=True)
class _ParsedDeviceInfo:
    serial_number: int
    firmware: VersionField
    hardware: VersionField
    mechanical: VersionField
    node_id: int
    side_code: int
    sensor_variant: str
    origin: str
    raw: bytes


def l30_identity_transaction(
    capabilities: DeviceCapabilities,
    *,
    node_id: int,
    host_id: int,
) -> ProtocolTransaction:
    """Read DeviceInfo and product code before publishing verified identity."""

    if capabilities.model != "L30":
        raise ProtocolError("L30 identity transaction requires L30 capabilities")
    if capabilities.side is not HandSide.NOT_APPLICABLE:
        raise ProtocolError("L30 capabilities must use a side-independent mapping")
    state: dict[str, _ParsedDeviceInfo] = {}

    def consume_product_code(message: object) -> ResponseProgress:
        data = matching_response_data(
            message,
            parent=PARENT_CONFIG,
            subcommand=SUB_PRODUCT_CODE,
            write=False,
            node_id=node_id,
            host_id=host_id,
        )
        if data is None:
            return ResponseProgress(matched=False)
        product_code, product_raw = decode_ascii_response(data)
        parsed = state.get("device_info")
        if parsed is None:
            raise ProtocolError("L30 product code arrived before DeviceInfo")
        return ResponseProgress(
            matched=True,
            complete=True,
            result=_identity_result(
                capabilities,
                parsed,
                product_code=product_code,
                product_raw=product_raw,
            ),
        )

    product_transaction = ProtocolTransaction(
        key=("identity", "l30", "product_code"),
        correlation_key=("l30_read", node_id, PARENT_CONFIG, SUB_PRODUCT_CODE),
        request_messages=(
            build_request(
                parent=PARENT_CONFIG,
                subcommand=SUB_PRODUCT_CODE,
                write=False,
                node_id=node_id,
                host_id=host_id,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.004,
        response_consumer=consume_product_code,
        late_response_guard_s=0.01,
    )

    def consume_device_info(message: object) -> ResponseProgress:
        data = matching_response_data(
            message,
            parent=PARENT_CONFIG,
            subcommand=SUB_DEVICE_INFO,
            write=False,
            node_id=node_id,
            host_id=host_id,
        )
        if data is None:
            return ResponseProgress(matched=False)
        raw = decode_response_payload(data, expected_length=DEVICE_INFO_LENGTH)
        state["device_info"] = _parse_device_info(raw, expected_node_id=node_id)
        return ResponseProgress(matched=True, complete=True)

    return ProtocolTransaction(
        key=("identity", "l30"),
        correlation_key=("l30_read", node_id, PARENT_CONFIG, SUB_DEVICE_INFO),
        request_messages=(
            build_request(
                parent=PARENT_CONFIG,
                subcommand=SUB_DEVICE_INFO,
                write=False,
                node_id=node_id,
                host_id=host_id,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.008,
        response_consumer=consume_device_info,
        late_response_guard_s=0.01,
        next_transaction=product_transaction,
    )


def _parse_device_info(data: bytes, *, expected_node_id: int) -> _ParsedDeviceInfo:
    if len(data) != DEVICE_INFO_LENGTH:
        raise ProtocolError(f"L30 DeviceInfo must contain {DEVICE_INFO_LENGTH} bytes")
    if data[0] != _PRODUCT_ID:
        raise IdentityMismatchError(
            f"expected L30 product ID 0x{_PRODUCT_ID:02X}, got 0x{data[0]:02X}"
        )
    node_id = data[14]
    if node_id != expected_node_id:
        raise IdentityMismatchError(
            f"L30 DeviceInfo node ID {node_id} does not match endpoint {expected_node_id}"
        )
    side_code = data[15]
    if side_code not in _SIDE_LABELS:
        raise ProtocolError(f"L30 DeviceInfo has unknown hand type {side_code}")
    return _ParsedDeviceInfo(
        serial_number=int.from_bytes(data[1:5], "big"),
        firmware=_version(data[5:8]),
        hardware=_version(data[8:11]),
        mechanical=_version(data[11:14]),
        node_id=node_id,
        side_code=side_code,
        sensor_variant=_ascii_byte(data[16], "sensor type"),
        origin=_ascii_byte(data[17], "origin"),
        raw=data,
    )


def _identity_result(
    declared: DeviceCapabilities,
    parsed: _ParsedDeviceInfo,
    *,
    product_code: str,
    product_raw: bytes,
) -> IdentityResult:
    _verify_product_code(product_code, parsed)
    serial_number = str(parsed.serial_number)
    identity = replace(
        declared.identity,
        verified=True,
        firmware=parsed.firmware.text,
        hardware_revision=parsed.hardware.text,
        mechanical_revision=parsed.mechanical.text,
        sensor_variant=parsed.sensor_variant,
        serial_number=serial_number,
        product_code=product_code,
    )
    verified = replace(declared, identity=identity)
    info = DeviceInfo(
        model=verified.model,
        serial_number=serial_number,
        pcb_version=parsed.hardware,
        firmware_version=parsed.firmware,
        mechanical_version=parsed.mechanical,
        product_code=product_code,
        sensor_variant=parsed.sensor_variant,
        side=HandSide.NOT_APPLICABLE,
        raw_identity=parsed.raw + product_raw,
        meta=SampleMeta.unavailable(),
    )
    return IdentityResult(
        capabilities=verified,
        device_info=info,
        fingerprint=(_PRODUCT_ID, parsed.serial_number, product_code),
    )


def _verify_product_code(value: str, parsed: _ParsedDeviceInfo) -> None:
    parts = value.split("-")
    if len(parts) != 7 or parts[0] != "LHT30":
        raise IdentityMismatchError(f"unexpected L30 product code {value!r}")
    if not parts[1].isdigit() or not parts[2].isdigit():
        raise ProtocolError("L30 product version and serial segments must be numeric")
    expected = (
        parsed.serial_number,
        _SIDE_LABELS[parsed.side_code],
        parsed.sensor_variant,
        "3",
        parsed.origin,
    )
    actual = (int(parts[2]), parts[3], parts[4], parts[5], parts[6])
    if actual != expected:
        raise IdentityMismatchError(
            "L30 product code disagrees with DeviceInfo identity fields"
        )


def _version(raw: bytes) -> VersionField:
    if len(raw) != 3:
        raise ProtocolError("L30 version field must contain three bytes")
    major, minor, patch = raw
    return VersionField(
        raw=tuple(raw),
        text=f"V{major}.{minor}.{patch}",
        major=major,
        minor=minor,
        patch=patch,
    )


def _ascii_byte(value: int, label: str) -> str:
    try:
        decoded = bytes((value,)).decode("ascii")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"L30 {label} is not ASCII") from error
    if not decoded.isprintable():
        raise ProtocolError(f"L30 {label} is not printable ASCII")
    return decoded


__all__ = ["l30_identity_transaction"]
