"""Response-gated O30i identity and firmware-profile verification."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...api.device_info import DeviceInfo, VersionField
from ...api.errors import (
    IdentityMismatchError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from ...api.readings import SampleMeta
from ...capabilities.types import DeviceCapabilities
from ..base import IdentityResult, ProtocolTransaction, ResponseProgress
from .codec import MI_PRODUCT_INFO, MI_SENSOR_INFO
from .framing import RESPONSE_ID, build_read_message
from .response import HOPRangeAssembler

_EXPECTED_PROTOCOL_NAME = "HOP"
_SUPPORTED_PROTOCOL_VERSION = "0.0.3"


@dataclass(frozen=True, slots=True)
class _IdentityField:
    name: str
    main_index: int
    sub_index: int
    length: int


_FIELDS = (
    _IdentityField("product_model", MI_PRODUCT_INFO, 0x00, 0x10),
    _IdentityField("device_uid", MI_PRODUCT_INFO, 0x39, 0x20),
    _IdentityField("protocol_name", MI_PRODUCT_INFO, 0x59, 0x08),
    _IdentityField("protocol_version", MI_PRODUCT_INFO, 0x61, 0x08),
    _IdentityField("interface_hardware", MI_PRODUCT_INFO, 0x69, 0x08),
    _IdentityField("application_version", MI_PRODUCT_INFO, 0x89, 0x08),
    _IdentityField("mechanical_version", MI_PRODUCT_INFO, 0x91, 0x08),
    _IdentityField("sensor_variant", MI_SENSOR_INFO, 0x00, 0x20),
)


def o30i_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Read identity as bounded single-frame fields and select the HOP profile."""

    if capabilities.model != "O30i":
        raise ProtocolError("O30i identity transaction requires O30i capabilities")
    values: dict[str, tuple[str, bytes]] = {}
    return _field_transaction(0, capabilities, values)


def _field_transaction(
    index: int,
    capabilities: DeviceCapabilities,
    values: dict[str, tuple[str, bytes]],
) -> ProtocolTransaction:
    field = _FIELDS[index]
    assembler = HOPRangeAssembler(
        field.main_index,
        sub_index=field.sub_index,
        length=field.length,
        rts=False,
    )
    next_transaction = (
        _field_transaction(index + 1, capabilities, values)
        if index + 1 < len(_FIELDS)
        else None
    )

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        text = _decode_required_ascii(complete, field.name)
        _validate_identity_field(field.name, text)
        values[field.name] = (text, complete)
        result = None
        if next_transaction is None:
            result = _identity_result(capabilities, values)
        return ResponseProgress(matched=True, complete=True, result=result)

    return ProtocolTransaction(
        key=("identity", "o30i", field.name),
        correlation_key=("o30i_hop", RESPONSE_ID),
        request_messages=(
            build_read_message(
                field.main_index,
                sub_index=field.sub_index,
                length=field.length,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.003,
        response_consumer=consume,
        late_response_guard_s=0.01,
        next_transaction=next_transaction,
    )


def _identity_result(
    declared: DeviceCapabilities,
    values: dict[str, tuple[str, bytes]],
) -> IdentityResult:
    text = {name: value[0] for name, value in values.items()}
    raw = {name: value[1] for name, value in values.items()}
    firmware = _version(raw["application_version"], text["application_version"])
    hardware = _version(raw["interface_hardware"], text["interface_hardware"])
    mechanical = _version(raw["mechanical_version"], text["mechanical_version"])
    verified_identity = replace(
        declared.identity,
        verified=True,
        firmware=firmware.text,
        hardware_revision=hardware.text,
        mechanical_revision=mechanical.text,
        sensor_variant=text["sensor_variant"],
        serial_number=text["device_uid"],
        product_code=None,
    )
    verified = replace(declared, identity=verified_identity)
    raw_identity = b"".join(raw[field.name] for field in _FIELDS)
    info = DeviceInfo(
        model=verified.model,
        serial_number=text["device_uid"],
        pcb_version=hardware,
        firmware_version=firmware,
        mechanical_version=mechanical,
        product_code=None,
        sensor_variant=text["sensor_variant"],
        side=verified.side,
        raw_identity=raw_identity,
        meta=SampleMeta.unavailable(),
    )
    fingerprint = (
        text["product_model"],
        text["device_uid"],
        text["protocol_name"],
        text["protocol_version"],
        hardware.text,
        firmware.text,
        mechanical.text,
        text["sensor_variant"],
    )
    return IdentityResult(
        capabilities=verified,
        device_info=info,
        fingerprint=fingerprint,
    )


def _validate_identity_field(name: str, value: str) -> None:
    if name == "product_model" and value.casefold() != "o30i":
        raise IdentityMismatchError(f"expected O30i product model, got {value!r}")
    if name == "protocol_name" and value.upper() != _EXPECTED_PROTOCOL_NAME:
        raise UnsupportedFirmwareError(f"O30i protocol {value!r} is unsupported")
    if name == "protocol_version" and value != _SUPPORTED_PROTOCOL_VERSION:
        raise UnsupportedFirmwareError(
            f"O30i HOP protocol version {value!r} is unsupported"
        )


def _decode_required_ascii(data: bytes, name: str) -> str:
    try:
        value = data.split(b"\x00", 1)[0].decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ProtocolError(f"O30i {name} is not ASCII") from error
    if not value:
        raise ProtocolError(f"O30i {name} is empty")
    return value


def _version(raw: bytes, text: str) -> VersionField:
    semantic = text.removeprefix("V").removeprefix("M")
    parts = semantic.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        major, minor, patch = (int(part) for part in parts)
        return VersionField(
            raw=raw,
            text=text,
            major=major,
            minor=minor,
            patch=patch,
        )
    return VersionField(raw=raw, text=text)


__all__ = ["o30i_identity_transaction"]
