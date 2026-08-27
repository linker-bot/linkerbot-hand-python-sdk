"""O20 fixed-width identity parsing and verification transaction."""

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
    REG_DEVICE_INFO,
    build_read_message,
    device_id_for_side,
    matching_read_data,
)

_PRODUCT_LENGTH = 10
_SERIAL_LENGTH = 20
_SOFTWARE_LENGTH = 10
_HARDWARE_LENGTH = 10
_UID_LENGTH = 11
_RIGHT_SIDE_CODE = 1
_LEFT_SIDE_CODE = 2


def o20_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build a fresh O20 DeviceInfo read and configured-side verification."""

    if capabilities.model != "O20":
        raise ProtocolError("O20 identity transaction requires O20 capabilities")
    device_id = device_id_for_side(capabilities.side)
    route = ("o20", device_id, REG_DEVICE_INFO)

    def consume(message: object) -> ResponseProgress:
        matched = matching_read_data(
            message,
            device_id=device_id,
            register=REG_DEVICE_INFO,
        )
        if matched is None:
            return ResponseProgress(matched=False)
        identity = _parse_identity(matched[1], configured_side=capabilities.side)
        verified_identity = replace(
            capabilities.identity,
            verified=True,
            firmware=identity.firmware.text,
            hardware_revision=identity.hardware.text,
            serial_number=identity.serial_number,
            product_code=identity.product_model,
        )
        verified = replace(capabilities, identity=verified_identity)
        info = DeviceInfo(
            model=verified.model,
            serial_number=identity.serial_number,
            pcb_version=identity.hardware,
            firmware_version=identity.firmware,
            mechanical_version=None,
            product_code=identity.product_model,
            sensor_variant=None,
            side=identity.side,
            raw_identity=identity.raw,
            meta=SampleMeta.unavailable(),
        )
        return ResponseProgress(
            matched=True,
            complete=True,
            result=IdentityResult(
                capabilities=verified,
                device_info=info,
                fingerprint=(
                    identity.product_model,
                    identity.side.value,
                    identity.serial_number,
                    identity.uid,
                ),
            ),
        )

    return ProtocolTransaction(
        key=("identity", route),
        correlation_key=("identity", route),
        request_messages=(build_read_message(device_id, REG_DEVICE_INFO),),
        response_timeout_s=0.1,
        estimated_duration_s=0.004,
        response_consumer=consume,
        late_response_guard_s=0.01,
    )


@dataclass(frozen=True, slots=True)
class _ParsedIdentity:
    """Fields decoded from one exact DeviceInfo payload."""

    product_model: str
    serial_number: str
    firmware: VersionField
    hardware: VersionField
    side: HandSide
    uid: bytes
    raw: bytes


def _parse_identity(data: bytes, *, configured_side: HandSide) -> _ParsedIdentity:
    if len(data) < DEVICE_INFO_LENGTH:
        raise ProtocolError(
            f"O20 DeviceInfo must contain at least {DEVICE_INFO_LENGTH} bytes"
        )
    raw = data[:DEVICE_INFO_LENGTH]
    cursor = 0
    product_raw = raw[cursor : cursor + _PRODUCT_LENGTH]
    cursor += _PRODUCT_LENGTH
    serial_raw = raw[cursor : cursor + _SERIAL_LENGTH]
    cursor += _SERIAL_LENGTH
    software_raw = raw[cursor : cursor + _SOFTWARE_LENGTH]
    cursor += _SOFTWARE_LENGTH
    hardware_raw = raw[cursor : cursor + _HARDWARE_LENGTH]
    cursor += _HARDWARE_LENGTH
    side_code = raw[cursor]
    cursor += 1
    uid = raw[cursor : cursor + _UID_LENGTH]

    product = _decode_required_ascii(product_raw, "product model")
    normalized_product = product.upper()
    if normalized_product != "O20" and not normalized_product.startswith("O20-"):
        raise IdentityMismatchError(f"expected O20 product model, got {product}")
    side = _parse_side(side_code)
    if side is not configured_side:
        raise IdentityMismatchError(
            "O20 DeviceInfo side does not match the configured endpoint"
        )
    product_side = {"O20-L": HandSide.LEFT, "O20-R": HandSide.RIGHT}.get(
        normalized_product
    )
    if product_side is not None and product_side is not side:
        raise IdentityMismatchError("O20 product model and DeviceInfo side disagree")
    serial = _decode_required_ascii(serial_raw, "serial number")
    software_text = _decode_required_ascii(software_raw, "software version")
    hardware_text = _decode_required_ascii(hardware_raw, "hardware version")
    return _ParsedIdentity(
        product_model=product,
        serial_number=serial,
        firmware=VersionField(raw=software_raw, text=software_text),
        hardware=VersionField(raw=hardware_raw, text=hardware_text),
        side=side,
        uid=uid,
        raw=raw,
    )


def _decode_required_ascii(data: bytes, label: str) -> str:
    try:
        value = data.split(b"\x00", 1)[0].decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ProtocolError(f"O20 {label} is not ASCII") from error
    if not value:
        raise ProtocolError(f"O20 {label} is empty")
    return value


def _parse_side(value: int) -> HandSide:
    if value == _LEFT_SIDE_CODE:
        return HandSide.LEFT
    if value == _RIGHT_SIDE_CODE:
        return HandSide.RIGHT
    raise ProtocolError(f"O20 DeviceInfo contains unknown side code {value}")


__all__ = ["o20_identity_transaction"]
