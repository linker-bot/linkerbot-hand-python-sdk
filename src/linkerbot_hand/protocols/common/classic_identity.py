"""Identity transaction shared by classic CAN profiles with common C0/C1/C2/C4."""

from __future__ import annotations

from dataclasses import replace

from ...api.device_info import DeviceInfo
from ...api.errors import ProtocolError
from ...api.readings import SampleMeta
from ...capabilities.types import DeviceCapabilities
from ..base import IdentityResult, ProtocolTransaction, ResponseProgress
from .classic_can import (
    ClassicIdentityCollector,
    build_classic_message,
    classic_route,
    parse_factory_identity,
)


def basic_classic_identity_transaction(
    capabilities: DeviceCapabilities,
    *,
    public_model: str,
    factory_models: tuple[str, ...],
    l6_pcb_format: bool = False,
    allow_short_product_code: bool = False,
) -> ProtocolTransaction:
    """Build identity verification for the common classic CAN wire format.

    ``l6_pcb_format`` is L6's one wire quirk: its PCB version object uses a
    different layout, and its C1 request carries a subindex (``C1 01``) rather
    than a bare ``C1``. The two switch together, so a single flag drives both
    the collector's parsing and the request frame.
    """

    if capabilities.model != public_model:
        raise ProtocolError(
            f"{public_model} identity transaction requires {public_model} capabilities"
        )
    route = classic_route(capabilities.side)
    collector = ClassicIdentityCollector(
        route,
        l6_pcb_format=l6_pcb_format,
        allow_short_product_code=allow_short_product_code,
    )
    version_request = b"\xc1\x01" if l6_pcb_format else b"\xc1"

    def consume(message: object) -> ResponseProgress:
        if not collector.offer(message):
            return ResponseProgress(matched=False)
        if not collector.complete:
            return ResponseProgress(matched=True)
        product = parse_factory_identity(
            collector.product_code(),
            expected_model=factory_models,
            configured_side=capabilities.side,
        )
        pcb = collector.version(0xC1)
        firmware = collector.version(0xC2)
        mechanical = collector.version(0xC4)
        identity = replace(
            capabilities.identity,
            verified=True,
            firmware=firmware.text,
            hardware_revision=pcb.text,
            mechanical_revision=mechanical.text,
            sensor_variant=product.sensor_variant,
            serial_number=product.serial_number,
            product_code=product.product_code,
        )
        verified = replace(capabilities, identity=identity)
        info = DeviceInfo(
            model=verified.model,
            serial_number=product.serial_number,
            pcb_version=pcb,
            firmware_version=firmware,
            mechanical_version=mechanical,
            product_code=product.product_code,
            sensor_variant=product.sensor_variant,
            side=product.side,
            raw_identity=collector.product_code_bytes,
            meta=SampleMeta.unavailable(),
        )
        return ResponseProgress(
            matched=True,
            complete=True,
            result=IdentityResult(
                capabilities=verified,
                device_info=info,
                fingerprint=product.product_code,
            ),
        )

    return ProtocolTransaction(
        key=("identity", route),
        correlation_key=("identity", route),
        request_messages=tuple(
            build_classic_message(route, payload)
            for payload in (b"\xc0", version_request, b"\xc2", b"\xc4")
        ),
        response_timeout_s=0.1,
        estimated_duration_s=0.03,
        response_consumer=consume,
        request_interval_s=0.005,
        late_response_guard_s=0.02,
    )


__all__ = ["basic_classic_identity_transaction"]
