"""L6/O6 HOP profiles: shared objects, six installed logical positions."""

from dataclasses import replace

from ...api.commands import AckSupport
from ...api.enums import Confidence, HandSide
from ..types import (
    CommandSemantic,
    DeviceCapabilities,
    OperationCapabilities,
    TelemetrySemantic,
)
from ._common import U8_RANGE, angle_command, angle_telemetry, command, telemetry
from ._hop import shared_capabilities
from .l6 import declared_capabilities as legacy_l6
from .o6 import declared_capabilities as legacy_o6


def declared_capabilities(model: str, side: HandSide) -> DeviceCapabilities:
    """Keep public six-joint order; never copy legacy scales or fault bits."""
    legacy = (legacy_l6 if model == "L6" else legacy_o6)(side)
    joints = tuple(
        replace(
            j,
            percent_mapping=replace(
                j.percent_mapping, confidence=Confidence.APPROXIMATE
            ),
        )
        if j.percent_mapping is not None
        else j
        for j in legacy.joints
    )
    template = shared_capabilities(
        model,
        side,
        joints,
        raw_encoding="u8_hop_v36",
        addressing_note="HOP default standard request ID 1 for both sides; response=request|0x400; BRS off",
    )
    names = legacy.joint_names
    note = (
        "HOP v0.0.4 provisional raw 0..255; physical scale unverified; "
        "six public joints occupy logical positions 10,5,11,12,13,14"
    )
    commands = tuple(
        replace(
            c,
            channels=names,
            raw_encoding="u8_hop_v36",
            raw_scale_note=note,
            unit_confidence=Confidence.UNKNOWN,
        )
        for c in template.commands
        if c.semantic is not CommandSemantic.ANGLES
    )
    channels = tuple(
        replace(
            t,
            channels=names,
            shape=(6,),
            raw_encoding="u8_hop_v36",
            raw_scale_note=note,
            raw_unit=None,
            unit_confidence=Confidence.UNKNOWN,
            estimated_transaction_s=0.008,
        )
        if t.semantic is not TelemetrySemantic.FORCE
        else replace(t, raw_encoding="hop_tactile_descriptor")
        for t in template.telemetry
        if t.semantic is not TelemetrySemantic.ANGLES
    )
    if model == "L6":
        for cs, ts in (
            (CommandSemantic.POSITION_KP, TelemetrySemantic.POSITION_KP),
            (CommandSemantic.POSITION_KD, TelemetrySemantic.POSITION_KD),
        ):
            commands += (
                command(
                    cs,
                    names,
                    U8_RANGE,
                    "u16_le_hop_v36",
                    raw_unit=None,
                    raw_scale_note=note,
                    unit_confidence=Confidence.UNKNOWN,
                    ack_support=AckSupport.SUPPORTED,
                ),
            )
            channels += (
                telemetry(
                    ts,
                    names,
                    "u16_le_hop_v36",
                    shape=(6,),
                    raw_range=U8_RANGE,
                    raw_unit=None,
                    raw_scale_note=note,
                    unit_confidence=Confidence.UNKNOWN,
                    recommended_hz=1,
                    stale_after_s=3,
                    estimated_transaction_s=0.008,
                    device_measured=False,
                ),
            )
        commands += (
            command(
                CommandSemantic.TORQUES,
                names,
                U8_RANGE,
                "u8_hop_v36",
                raw_unit=None,
                raw_scale_note="HOP MI 0x06 output/limit; raw 0 does not select position mode",
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
        )
    assert template.stall_protection is not None
    return replace(
        template,
        identity=replace(template.identity, model=model),
        joints=joints,
        commands=(
            angle_command(
                joints,
                "u8_hop_v36",
                ack_support=AckSupport.SUPPORTED,
                returns_current_state=True,
            ),
            *commands,
        ),
        telemetry=(
            angle_telemetry(
                joints, "u8_hop_v36", recommended_hz=30, estimated_transaction_s=0.008
            ),
            *channels,
        ),
        stall_protection=replace(template.stall_protection, channels=names),
        transport=replace(
            template.transport,
            addressing_note=(
                "HOP CAN FD standard request ID defaults to 1 for both sides; "
                "response=request|0x400; side comes from identity; BRS defaults off"
            ),
        ),
        operations=OperationCapabilities(
            save_parameters=True,
            read_stall_protection=True,
            write_stall_protection=True,
            acknowledged_operations=("save_parameters",),
            read_can_configuration=True,
            write_can_configuration=True,
        ),
    )


__all__ = ["declared_capabilities"]
