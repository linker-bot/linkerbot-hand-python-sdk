"""Response-gated O30 identity with deployed and documented HOP layouts."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from ...api.device_info import DeviceInfo, VersionField
from ...api.enums import HandSide
from ...api.errors import (
    IdentityMismatchError,
    ProtocolError,
    UnsupportedFirmwareError,
)
from ...api.readings import SampleMeta
from ...capabilities.types import DeviceCapabilities
from ..base import IdentityResult, ProtocolTransaction, ResponseProgress
from .codec import MI_PRODUCT_INFO
from .framing import RESPONSE_ID, build_read_message
from .response import HOPRangeAssembler

_EXPECTED_PROTOCOL_NAME = "HOP"
_VERSION_PATTERN = re.compile(r"^[VM]?\d+(?:\.\d+){1,2}$", re.IGNORECASE)
_DATE_PREFIX_PATTERN = re.compile(r"^\d{4}-\d{2}")


@dataclass(frozen=True, slots=True)
class _IdentityField:
    name: str
    sub_index: int
    length: int
    required: bool = False


_COMMON_FIELDS = (
    _IdentityField("product_model", 0x00, 0x10, required=True),
    _IdentityField("power_supply_range", 0x10, 0x10),
    _IdentityField("microcontroller_uid", 0x20, 0x19),
    _IdentityField("device_uid", 0x39, 0x20, required=True),
    _IdentityField("protocol_name", 0x59, 0x08, required=True),
    _IdentityField("protocol_version", 0x61, 0x08, required=True),
    _IdentityField("board_layout_probe", 0x69, 0x10, required=True),
    _IdentityField("version_layout_probe", 0x99, 0x08),
)

_DEPLOYED_FIELDS = (
    _IdentityField("control_board_version", 0x79, 0x08),
    _IdentityField("bootloader_version", 0x81, 0x08),
    _IdentityField("application_version", 0x89, 0x08, required=True),
    _IdentityField("mechanical_version", 0x91, 0x08),
    _IdentityField("build_time", 0x99, 0x20),
    _IdentityField("supported_interfaces", 0xB9, 0x20),
    _IdentityField("reported_side", 0xD9, 0x08),
)

_DOCUMENTED_FIELDS = (
    _IdentityField("adapter_board_version", 0x79, 0x10),
    _IdentityField("control_board_version", 0x89, 0x10),
    _IdentityField("application_version", 0xA1, 0x08, required=True),
    _IdentityField("mechanical_version", 0xA9, 0x08),
    _IdentityField("build_time", 0xB1, 0x20),
    _IdentityField("supported_interfaces", 0xD1, 0x20),
    _IdentityField("reported_side", 0xF1, 0x08),
)


def o30_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Read identity without imposing a firmware-version connection gate."""

    if capabilities.model != "O30":
        raise ProtocolError("O30 identity transaction requires O30 capabilities")
    values: dict[str, bytes] = {}
    return _field_transaction(
        _COMMON_FIELDS,
        0,
        capabilities,
        values,
        next_factory=lambda: _layout_transaction(capabilities, values),
    )


def _layout_transaction(
    capabilities: DeviceCapabilities,
    values: dict[str, bytes],
) -> ProtocolTransaction:
    layout = _select_layout(values)
    fields = _DEPLOYED_FIELDS if layout == "deployed" else _DOCUMENTED_FIELDS
    return _field_transaction(
        fields,
        0,
        capabilities,
        values,
        result_factory=lambda: _identity_result(
            capabilities,
            values,
            layout=layout,
        ),
    )


def _field_transaction(
    fields: tuple[_IdentityField, ...],
    index: int,
    capabilities: DeviceCapabilities,
    values: dict[str, bytes],
    *,
    next_factory: Callable[[], ProtocolTransaction] | None = None,
    result_factory: Callable[[], IdentityResult] | None = None,
) -> ProtocolTransaction:
    field = fields[index]
    assembler = HOPRangeAssembler(
        MI_PRODUCT_INFO,
        sub_index=field.sub_index,
        length=field.length,
        rts=False,
    )
    final_field = index + 1 == len(fields)

    def build_next() -> ProtocolTransaction | None:
        if not final_field:
            return _field_transaction(
                fields,
                index + 1,
                capabilities,
                values,
                next_factory=next_factory,
                result_factory=result_factory,
            )
        if next_factory is not None:
            return next_factory()
        return None

    def consume(message: object) -> ResponseProgress:
        matched, complete = assembler.consume(message)
        if complete is None:
            return ResponseProgress(matched=matched)
        text = _decode_ascii(complete, field.name)
        if field.required and text is None:
            raise ProtocolError(f"O30 {field.name} is empty")
        if text is not None:
            _validate_identity_field(field.name, text)
        values[field.name] = complete
        result = (
            result_factory()
            if final_field and next_factory is None and result_factory is not None
            else None
        )
        return ResponseProgress(matched=True, complete=True, result=result)

    has_next = not final_field or next_factory is not None
    return ProtocolTransaction(
        key=("identity", "o30", field.name),
        correlation_key=("o30_hop", RESPONSE_ID),
        request_messages=(
            build_read_message(
                MI_PRODUCT_INFO,
                sub_index=field.sub_index,
                length=field.length,
            ),
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.003,
        response_consumer=consume,
        late_response_guard_s=0.01,
        next_transaction_factory=build_next if has_next else None,
    )


def _select_layout(values: dict[str, bytes]) -> str:
    protocol_version = _required_text(values, "protocol_version")
    board_probe = values["board_layout_probe"]
    version_probe = _decode_ascii(
        values["version_layout_probe"],
        "version_layout_probe",
    )
    second_board_field = _decode_ascii(
        board_probe[8:],
        "adapter_board_version_probe",
    )
    if (
        _semantic_version(protocol_version) == (0, 0, 3)
        or _looks_like_version(second_board_field)
        or (
            version_probe is not None
            and _DATE_PREFIX_PATTERN.match(version_probe) is not None
        )
    ):
        return "deployed"
    return "documented"


def _identity_result(
    declared: DeviceCapabilities,
    values: dict[str, bytes],
    *,
    layout: str,
) -> IdentityResult:
    product_model = _required_text(values, "product_model")
    device_uid = _required_text(values, "device_uid")
    protocol_name = _required_text(values, "protocol_name")
    protocol_version = _required_text(values, "protocol_version")
    board_probe = values["board_layout_probe"]

    if layout == "deployed":
        interface_text = _decode_ascii(
            board_probe[:8],
            "interface_board_version",
        )
        adapter_text = _decode_ascii(
            board_probe[8:],
            "adapter_board_version",
        )
        interface_raw = board_probe[:8]
        adapter_raw = board_probe[8:]
        bootloader_raw = values["bootloader_version"]
    else:
        interface_text = _decode_ascii(
            board_probe,
            "interface_board_version",
        )
        adapter_text = _decode_ascii(
            values["adapter_board_version"],
            "adapter_board_version",
        )
        interface_raw = board_probe
        adapter_raw = values["adapter_board_version"]
        bootloader_raw = values["version_layout_probe"]

    control_text = _optional_text(values, "control_board_version")
    bootloader_text = _decode_ascii(bootloader_raw, "bootloader_version")
    application_text = _required_text(values, "application_version")
    mechanical_text = _optional_text(values, "mechanical_version")
    build_time = _optional_text(values, "build_time")
    supported_interfaces = _optional_text(values, "supported_interfaces")
    power_supply_range = _optional_text(values, "power_supply_range")
    microcontroller_uid = _optional_text(values, "microcontroller_uid")

    interface = _version_or_none(interface_raw, interface_text)
    adapter = _version_or_none(adapter_raw, adapter_text)
    control = _version_or_none(values["control_board_version"], control_text)
    bootloader = _version_or_none(bootloader_raw, bootloader_text)
    firmware = _version(values["application_version"], application_text)
    mechanical = _version_or_none(values["mechanical_version"], mechanical_text)

    side_field = _optional_text(values, "reported_side")
    field_side = _parse_side(side_field)
    product_side = _side_from_product_model(product_model)
    if (
        field_side is not None
        and product_side is not None
        and field_side is not product_side
    ):
        raise IdentityMismatchError("O30 product model and reported side disagree")
    reported_side = field_side or product_side
    resolved_side = reported_side or declared.side

    verified_identity = replace(
        declared.identity,
        side=resolved_side,
        verified=True,
        firmware=firmware.text,
        hardware_revision=None if interface is None else interface.text,
        mechanical_revision=None if mechanical is None else mechanical.text,
        sensor_variant=None,
        serial_number=device_uid,
        product_code=product_model,
        protocol_name=protocol_name,
        protocol_version=protocol_version,
        power_supply_range=power_supply_range,
        microcontroller_uid=microcontroller_uid,
        adapter_board_version=None if adapter is None else adapter.text,
        control_board_version=None if control is None else control.text,
        bootloader_version=None if bootloader is None else bootloader.text,
        build_time=build_time,
        supported_interfaces=supported_interfaces,
        reported_side=reported_side,
    )
    verified = replace(declared, identity=verified_identity)
    info = DeviceInfo(
        model=verified.model,
        serial_number=device_uid,
        pcb_version=interface,
        firmware_version=firmware,
        mechanical_version=mechanical,
        product_code=product_model,
        sensor_variant=None,
        side=resolved_side,
        raw_identity=_raw_identity(values, layout=layout),
        meta=SampleMeta.unavailable(),
        adapter_board_version=adapter,
        control_board_version=control,
        bootloader_version=bootloader,
        build_time=build_time,
        supported_interfaces=supported_interfaces,
        reported_side=reported_side,
        protocol_name=protocol_name,
        protocol_version=protocol_version,
        power_supply_range=power_supply_range,
        microcontroller_uid=microcontroller_uid,
    )
    fingerprint = (
        product_model,
        device_uid,
        protocol_name,
        protocol_version,
        None if interface is None else interface.text,
        None if adapter is None else adapter.text,
        None if control is None else control.text,
        None if bootloader is None else bootloader.text,
        firmware.text,
        None if mechanical is None else mechanical.text,
        None if reported_side is None else reported_side.value,
    )
    return IdentityResult(
        capabilities=verified,
        device_info=info,
        fingerprint=fingerprint,
    )


def _raw_identity(values: dict[str, bytes], *, layout: str) -> bytes:
    fields = _COMMON_FIELDS + (
        _DEPLOYED_FIELDS if layout == "deployed" else _DOCUMENTED_FIELDS
    )
    length = 0xE1 if layout == "deployed" else 0xF9
    raw = bytearray(length)
    for field in fields:
        data = values[field.name]
        raw[field.sub_index : field.sub_index + field.length] = data
    return bytes(raw)


def _validate_identity_field(name: str, value: str) -> None:
    normalized = value.casefold()
    if name == "product_model" and not (
        normalized in {"o30", "o30i"} or normalized.startswith(("o30-", "o30i-"))
    ):
        raise IdentityMismatchError(f"expected O30 product model, got {value!r}")
    if name == "protocol_name" and value.upper() != _EXPECTED_PROTOCOL_NAME:
        raise UnsupportedFirmwareError(f"O30 protocol {value!r} is unsupported")


def _required_text(values: dict[str, bytes], name: str) -> str:
    value = _optional_text(values, name)
    if value is None:
        raise ProtocolError(f"O30 {name} is empty")
    return value


def _optional_text(values: dict[str, bytes], name: str) -> str | None:
    return _decode_ascii(values[name], name)


def _decode_ascii(data: bytes, name: str) -> str | None:
    try:
        value = data.split(b"\x00", 1)[0].decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ProtocolError(f"O30 {name} is not ASCII") from error
    return value or None


def _semantic_version(value: str) -> tuple[int, int, int] | None:
    semantic = value.removeprefix("V").removeprefix("v")
    parts = semantic.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def _looks_like_version(value: str | None) -> bool:
    return value is not None and _VERSION_PATTERN.fullmatch(value) is not None


def _parse_side(value: str | None) -> HandSide | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"l", "left"}:
        return HandSide.LEFT
    if normalized in {"r", "right"}:
        return HandSide.RIGHT
    raise ProtocolError(f"O30 reported side {value!r} is invalid")


def _side_from_product_model(value: str) -> HandSide | None:
    parts = value.split("-")
    if len(parts) < 4:
        return None
    return {"L": HandSide.LEFT, "R": HandSide.RIGHT}.get(parts[3].upper())


def _version(raw: bytes, text: str) -> VersionField:
    semantic = text.removeprefix("V").removeprefix("v")
    semantic = semantic.removeprefix("M").removeprefix("m")
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


def _version_or_none(raw: bytes, text: str | None) -> VersionField | None:
    return None if text is None else _version(raw, text)


__all__ = ["o30_identity_transaction"]
