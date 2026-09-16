"""O6 identity transaction and sensor-variant profile selection."""

from __future__ import annotations

from dataclasses import replace

from ...api.device_info import DeviceInfo
from ...api.errors import ProtocolError
from ...api.readings import SampleMeta
from ...capabilities.types import DeviceCapabilities
from ..base import IdentityResult, ProtocolTransaction, ResponseProgress
from ..common.classic_can import (
    ClassicIdentityCollector,
    build_classic_message,
    classic_route,
    parse_factory_identity,
)


def o6_identity_transaction(
    capabilities: DeviceCapabilities,
) -> ProtocolTransaction:
    """Build the complete O6 product-code and version transaction."""

    if capabilities.model != "O6":
        raise ProtocolError("O6 identity transaction requires O6 capabilities")
    route = classic_route(capabilities.side)
    collector = ClassicIdentityCollector(route, l6_pcb_format=False)

    def consume(message: object) -> ResponseProgress:
        if not collector.offer(message):
            return ResponseProgress(matched=False)
        if not collector.complete:
            return ResponseProgress(matched=True)
        return ResponseProgress(
            matched=True,
            complete=True,
            result=_identity_result(capabilities, collector),
        )

    requests = tuple(
        build_classic_message(route, bytes((opcode,)))
        for opcode in (0xC0, 0xC1, 0xC2, 0xC4)
    )
    return ProtocolTransaction(
        key=("identity", route),
        correlation_key=("identity", route),
        request_messages=requests,
        response_timeout_s=0.1,
        estimated_duration_s=0.03,
        response_consumer=consume,
        request_interval_s=0.005,
        late_response_guard_s=0.02,
    )


def _identity_result(
    declared: DeviceCapabilities,
    collector: ClassicIdentityCollector,
) -> IdentityResult:
    product = parse_factory_identity(
        collector.product_code(),
        expected_model="LHO6",
        configured_side=declared.side,
    )
    pcb = collector.version(0xC1)
    firmware = collector.version(0xC2)
    mechanical = collector.version(0xC4)
    identity = replace(
        declared.identity,
        verified=True,
        firmware=firmware.text,
        hardware_revision=pcb.text,
        mechanical_revision=mechanical.text,
        sensor_variant=product.sensor_variant,
        serial_number=product.serial_number,
        product_code=product.product_code,
    )
    # The factory code does not gate the tactile channel. Sensor variants other
    # than 04-B carry tactile hardware too, and the code never described the
    # matrix layout anyway. A hand whose firmware does not answer the tactile
    # query is downgraded at runtime by the firmware-optional path instead.
    verified = replace(declared, identity=identity)
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
    return IdentityResult(
        capabilities=verified,
        device_info=info,
        fingerprint=product.product_code,
    )


__all__ = ["o6_identity_transaction"]
