from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from linkerbot_hand.api import (
    AckSupport,
    Confidence,
    HandSide,
    UnsupportedOperationError,
)
from linkerbot_hand.api.errors import ValidationError
from linkerbot_hand.capabilities.registry import (
    SUPPORTED_MODELS,
    get_declared_capabilities,
)
from linkerbot_hand.capabilities.types import (
    CommandSemantic,
    TelemetrySemantic,
)
from linkerbot_hand.conversion.percent import (
    percent_to_raw_angles,
    raw_angles_to_percent,
    raw_angles_within_declared_range,
    validate_raw_angles,
)


@pytest.mark.parametrize(
    ("model", "joint_count"),
    [
        ("l6", 6),
        ("o6", 6),
        ("l20lite", 10),
        ("l25", 16),
        ("o20", 16),
        ("l30", 17),
        ("o30i", 20),
    ],
)
def test_all_declared_profiles_expose_ordered_joint_layout(
    model: str,
    joint_count: int,
) -> None:
    capabilities = get_declared_capabilities(
        model,
        side=None if model == "l30" else "left",
    )

    assert not capabilities.identity.verified
    assert len(capabilities.joints) == joint_count
    assert tuple(joint.index for joint in capabilities.joints) == tuple(
        range(joint_count)
    )
    assert capabilities.command(CommandSemantic.ANGLES) is not None
    assert capabilities.telemetry_channel(TelemetrySemantic.ANGLES) is not None
    with pytest.raises(FrozenInstanceError):
        capabilities.joints = ()  # ty: ignore[invalid-assignment]


def test_supported_model_names_are_canonical_and_stable() -> None:
    assert SUPPORTED_MODELS == (
        "L6",
        "O6",
        "L20Lite",
        "L25",
        "O20",
        "L30",
        "O30i",
    )


def test_side_is_required_for_sided_models() -> None:
    with pytest.raises(ValidationError, match="side is required"):
        get_declared_capabilities("l6")
    with pytest.raises(ValidationError, match="left or right"):
        get_declared_capabilities("o6", side=HandSide.NOT_APPLICABLE)


def test_l30_rejects_left_or_right_and_uses_not_applicable() -> None:
    capabilities = get_declared_capabilities("l30")

    assert capabilities.identity.side is HandSide.NOT_APPLICABLE
    with pytest.raises(ValidationError, match="must not specify side"):
        get_declared_capabilities("l30", side="left")


def test_configured_and_measured_speed_semantics_are_not_interchangeable() -> None:
    l6 = get_declared_capabilities("l6", side="left")
    o6 = get_declared_capabilities("o6", side="right")
    o20 = get_declared_capabilities("o20", side="left")
    l30 = get_declared_capabilities("l30")

    l6_speed = l6.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS)
    assert l6_speed is not None
    assert not l6_speed.device_measured
    assert not l6_speed.device_queried
    assert l6.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert o6.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert o20.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is not None
    l30_configured = l30.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS)
    assert l30_configured is not None
    assert not l30_configured.device_queried
    assert l30_configured.read_mode.value == "command_cache"
    assert l30.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is not None


def test_limit_semantics_do_not_claim_true_torque() -> None:
    l6 = get_declared_capabilities("l6", side="left")
    l20lite = get_declared_capabilities("l20lite", side="left")
    l30 = get_declared_capabilities("l30")

    assert l6.command(CommandSemantic.CURRENT_LIMITS) is not None
    assert l20lite.command(CommandSemantic.EFFORT_LIMITS) is not None
    l30_limit = l30.command(CommandSemantic.CURRENT_LIMITS)
    assert l30_limit is not None
    assert l30_limit.raw_unit == "mA"
    assert l30.command(CommandSemantic.TORQUES) is None
    l30_limit_state = l30.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS)
    assert l30_limit_state is not None
    assert not l30_limit_state.device_queried
    assert l30_limit_state.read_mode.value == "command_cache"
    l30_current = l30.telemetry_channel(TelemetrySemantic.CURRENTS)
    assert l30_current is not None
    assert l30_current.raw_unit == "count"
    assert "6.5 mA/count" in (l30_current.raw_scale_note or "")


def test_o30i_stall_contract_maps_only_installed_channels() -> None:
    capabilities = get_declared_capabilities("o30i", side="right")
    stall = capabilities.stall_protection

    assert stall is not None
    assert stall.channels == capabilities.joint_names
    assert len(stall.channels) == 20
    assert not stall.safety_range_verified
    assert stall.detection_time.raw_unit is None
    assert capabilities.operations.read_stall_protection
    assert capabilities.operations.write_stall_protection


def test_o30i_writes_declare_matchable_hop_response_ack() -> None:
    capabilities = get_declared_capabilities("o30i", side="right")

    assert capabilities.commands
    assert all(
        command.ack_support is AckSupport.SUPPORTED for command in capabilities.commands
    )


def test_o6_declared_sensor_shape_matches_04_b_contract() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)

    assert force is not None
    assert force.shape == (5, 10, 4)


def test_l6_percent_mapping_matches_measured_raw_direction() -> None:
    capabilities = get_declared_capabilities("l6", side="left")

    assert percent_to_raw_angles((0, 50, 100, 100, 100, 100), capabilities) == (
        0,
        128,
        255,
        255,
        255,
        255,
    )
    assert raw_angles_to_percent((0, 127, 255, 255, 255, 255), capabilities) == (
        0.0,
        pytest.approx(49.8039215686),
        100.0,
        100.0,
        100.0,
        100.0,
    )
    thumb_abd = capabilities.joints[1].percent_mapping
    assert thumb_abd is not None
    assert thumb_abd.zero_label == "toward_thumb_web"
    assert thumb_abd.hundred_label == "away_from_thumb_web"
    assert thumb_abd.confidence is Confidence.MEASURED


def test_o20_flexion_percent_direction_is_inverse_of_raw_growth() -> None:
    capabilities = get_declared_capabilities("o20", side="right")

    assert percent_to_raw_angles((0,) * 16, capabilities)[0] == 120
    assert percent_to_raw_angles((100,) * 16, capabilities)[0] == 0
    assert raw_angles_to_percent((180,) * 16, capabilities)[5] == 0.0


def test_exact_raw_validation_does_not_apply_percent_direction() -> None:
    capabilities = get_declared_capabilities("l30")
    raw = tuple(joint.raw_angle_range.minimum for joint in capabilities.joints)

    assert validate_raw_angles(raw, capabilities) == raw
    with pytest.raises(ValidationError, match="outside"):
        validate_raw_angles((9999,) + raw[1:], capabilities)


def test_readback_preserves_out_of_range_evidence_without_clamping() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    feedback = (300, 0, 0, 0, 0, 0)

    assert raw_angles_to_percent(feedback, capabilities)[0] == pytest.approx(
        117.6470588235
    )
    assert not raw_angles_within_declared_range(feedback, capabilities)


def test_percent_input_is_strict_and_never_clamped() -> None:
    capabilities = get_declared_capabilities("l6", side="left")

    with pytest.raises(ValidationError, match="0 through 100"):
        percent_to_raw_angles((-1, 0, 0, 0, 0, 0), capabilities)
    with pytest.raises(ValidationError, match="finite"):
        percent_to_raw_angles((float("nan"), 0, 0, 0, 0, 0), capabilities)


def test_missing_percent_mapping_is_reported_as_unsupported() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    joints = (
        replace(capabilities.joints[0], percent_mapping=None),
        *capabilities.joints[1:],
    )
    angle_command = capabilities.command(CommandSemantic.ANGLES)
    assert angle_command is not None
    commands = tuple(
        replace(
            value,
            percent_mappings=(None, *value.percent_mappings[1:]),
        )
        if value.semantic is CommandSemantic.ANGLES
        and value.percent_mappings is not None
        else value
        for value in capabilities.commands
    )
    capabilities = replace(capabilities, joints=joints, commands=commands)

    with pytest.raises(UnsupportedOperationError):
        percent_to_raw_angles((0, 0, 0, 0, 0, 0), capabilities)
