from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from linkerbot_hand.api import (
    AckSupport,
    Confidence,
    HandSide,
    ReadMode,
    SampleProjection,
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
        ("l20", 16),
        ("o20", 16),
        ("l30", 17),
        ("o30", 20),
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
        "L20",
        "O20",
        "L30",
        "O30",
    )


@pytest.mark.parametrize(
    ("model", "canonical"),
    (
        ("O30", "O30"),
        ("o30", "O30"),
        ("L20", "L20"),
        ("l20", "L20"),
        ("L20Lite", "L20Lite"),
        ("l20lite", "L20Lite"),
        ("l20-lite", "L20Lite"),
        ("L20-LITE", "L20Lite"),
    ),
)
def test_public_model_spellings_return_canonical_capabilities(
    model: str, canonical: str
) -> None:
    assert get_declared_capabilities(model, side="left").model == canonical


@pytest.mark.parametrize(
    "model",
    (
        "O30i",
        "o30i",
        "L25",
        "l25",
        "LHT20",
        "LHL25",
        "LHG20",
        "O30-20-001-R-B",
        "O30i-20-001-R-B",
        "LHT20-10-499-L-B-1-D",
    ),
)
def test_product_codes_and_historical_names_are_not_configuration_aliases(
    model: str,
) -> None:
    with pytest.raises(ValidationError, match="unsupported hand model"):
        get_declared_capabilities(model, side="left")


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
    o30 = get_declared_capabilities("o30", side="right")

    l6_speed = l6.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS)
    assert l6_speed is not None
    assert not l6_speed.device_measured
    assert not l6_speed.device_queried
    assert l6_speed.read_mode is ReadMode.COMMAND_RESPONSE
    assert l6.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert o6.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is None
    assert o20.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is not None
    l30_configured = l30.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS)
    assert l30_configured is not None
    assert not l30_configured.device_queried
    assert l30_configured.read_mode.value == "command_cache"
    assert l30.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS) is not None
    o30_measured = o30.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS)
    o30_configured = o30.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS)
    assert o30_measured is not None and o30_measured.device_measured
    assert o30_configured is not None and not o30_configured.device_measured
    assert o30_measured.device_queried and o30_configured.device_queried


@pytest.mark.parametrize("model", ("l20lite", "l20"))
def test_l20_family_queries_return_measurements_not_configuration(model: str) -> None:
    """A bare speed or torque query on these two models returns the live value.

    The protocol states that a write carries the target and is echoed back,
    while a bare query returns the real-time value. Declaring either channel as
    configuration would present actual motion and load as "the value you just
    wrote", and would leave both models with no measured channel at all.
    """

    capabilities = get_declared_capabilities(model, side="left")

    measured = capabilities.telemetry_channel(TelemetrySemantic.MEASURED_SPEEDS)
    assert measured is not None
    assert measured.device_measured
    assert measured.device_queried
    assert capabilities.telemetry_channel(TelemetrySemantic.CONFIGURED_SPEEDS) is None

    current = capabilities.telemetry_channel(TelemetrySemantic.CURRENTS)
    assert current is not None
    assert current.device_measured
    assert current.device_queried
    assert current.raw_unit == "count"
    assert "real-time" in (current.raw_scale_note or "")
    assert capabilities.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS) is None
    assert capabilities.command(CommandSemantic.CURRENT_LIMITS) is not None


@pytest.mark.parametrize("model", ("l20lite", "l20"))
def test_l20_family_reads_are_device_responses_not_config_responses(model: str) -> None:
    from linkerbot_hand.protocols.l20 import codec as l20_codec
    from linkerbot_hand.protocols.l20lite import codec as l20lite_codec

    codec = l20lite_codec if model == "l20lite" else l20_codec

    assert codec.CONFIG_TELEMETRY == ()
    assert TelemetrySemantic.MEASURED_SPEEDS in codec.TELEMETRY_LAYOUTS
    assert TelemetrySemantic.CONFIGURED_SPEEDS not in codec.TELEMETRY_LAYOUTS


@pytest.mark.parametrize("model", ("l20lite", "l20"))
def test_l20_family_units_distinguish_sensor_counts_from_documented_celsius(
    model: str,
) -> None:
    capabilities = get_declared_capabilities(model, side="left")
    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    temperature = capabilities.telemetry_channel(TelemetrySemantic.TEMPERATURES)
    assert force is not None and force.raw_unit == "count"
    assert force.unit_confidence is Confidence.UNKNOWN
    assert "physical calibration unknown" in (force.raw_scale_note or "")
    assert temperature is not None and temperature.raw_unit == "degree_celsius"
    assert temperature.unit_confidence is Confidence.DOCUMENTED
    assert temperature.raw_scale_note == "1 degree Celsius/count"


def test_decoded_statuses_matches_the_o30_read_table() -> None:
    """The capability flag and the protocol read table must not drift apart.

    A consumer decides at configure time whether a channel will carry decoded
    statuses, so the declaration has to agree with whichever objects actually
    have a decoder wired. Inferring this from ``raw_encoding`` instead would
    silently miss a channel whose wire layout differs.
    """

    from linkerbot_hand.protocols.o30.telemetry import _READ_OBJECTS

    capabilities = get_declared_capabilities("o30", side="right")

    for semantic, profile in _READ_OBJECTS.items():
        channel = capabilities.telemetry_channel(semantic)
        assert channel is not None
        assert channel.decoded_statuses == (profile.decode_statuses is not None), (
            f"{semantic.value} disagrees between the read table and capabilities"
        )

    faults = capabilities.telemetry_channel(TelemetrySemantic.FAULTS)
    assert faults is not None and faults.decoded_statuses


@pytest.mark.parametrize(
    ("model", "side"),
    [
        ("l20", "right"),
        ("o20", "right"),
        ("l30", None),
    ],
)
def test_models_without_fault_bit_decoder_do_not_advertise_one(
    model: str, side: str | None
) -> None:
    capabilities = get_declared_capabilities(model, side=side)

    assert not any(channel.decoded_statuses for channel in capabilities.telemetry)


@pytest.mark.parametrize(
    ("model", "side", "projection"),
    [
        ("l6", "right", SampleProjection.TACTILE_MATRIX),
        ("o6", "right", SampleProjection.TACTILE_MATRIX),
        ("l20lite", "right", SampleProjection.TACTILE_MATRIX),
        ("l20", "right", SampleProjection.TACTILE_MATRIX),
        ("o20", "right", SampleProjection.TACTILE_MATRIX),
        ("l30", None, SampleProjection.TACTILE_MATRIX),
        ("o30", "right", SampleProjection.TACTILE_DESCRIPTOR),
    ],
)
def test_force_projection_is_declared_not_inferred(
    model: str,
    side: str | None,
    projection: SampleProjection,
) -> None:
    """Every force channel states which sample field it fills.

    Consumers previously reverse-engineered this from ``raw_encoding`` plus the
    number of ``shape`` dimensions. Those two are entangled: O30's descriptor
    force is one-dimensional, so shape alone cannot tell it from a scalar
    channel, and the encoding string is a wire-format detail.
    """

    capabilities = get_declared_capabilities(model, side=side)

    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    assert force is not None
    assert force.payload_projection is projection
    # The declaration must agree with the shape the profile actually publishes.
    if projection is SampleProjection.TACTILE_MATRIX:
        assert len(force.shape) == 3
    else:
        assert len(force.shape) == 1
    # Nothing but force uses a tactile projection.
    assert all(
        channel.payload_projection is SampleProjection.RAW_VECTOR
        for channel in capabilities.telemetry
        if channel.semantic is not TelemetrySemantic.FORCE
    )


@pytest.mark.parametrize(
    ("model", "side", "supported"),
    [
        ("l6", "left", False),
        ("o6", "left", False),
        ("l20lite", "left", False),
        ("l20", "left", False),
        ("o20", "left", False),
        ("l30", None, False),
        ("o30", "left", True),
        ("o30", "right", True),
    ],
)
def test_normal_force_resultant_support_follows_the_projection(
    model: str,
    side: str | None,
    supported: bool,
) -> None:
    """Support must be decided by the capability, never by the model name.

    The check runs against declared capabilities, so it resolves before
    ``open()``. A model without the descriptor projection is rejected as
    unsupported; a model with it gets past the check and then fails only because
    the device is still closed.
    """

    from linkerbot_hand import ClosedError, HandDevice, UnsupportedOperationError
    from linkerbot_transport.can import CANTransport
    from linkerbot_transport.canfd import SocketCANFDTransport

    transport = (
        SocketCANFDTransport("vcan-test", bitrate=1_000_000, data_bitrate=5_000_000)
        if model in {"o20", "l30", "o30"}
        else CANTransport("vcan-test")
    )
    device = HandDevice(model=model, side=side, transport=transport)

    expected = ClosedError if supported else UnsupportedOperationError
    with pytest.raises(expected):
        device.get_normal_force_resultant(timeout=0.1)


def test_limit_semantics_do_not_claim_true_torque() -> None:
    l6 = get_declared_capabilities("l6", side="left")
    l20lite = get_declared_capabilities("l20lite", side="left")
    l30 = get_declared_capabilities("l30")

    assert l6.command(CommandSemantic.CURRENT_LIMITS) is not None
    assert l20lite.command(CommandSemantic.CURRENT_LIMITS) is not None
    l30_limit = l30.command(CommandSemantic.CURRENT_LIMITS)
    assert l30_limit is not None
    assert l30_limit.raw_unit == "count"
    assert l30.command(CommandSemantic.TORQUES) is None
    l30_limit_state = l30.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS)
    assert l30_limit_state is not None
    assert not l30_limit_state.device_queried
    assert l30_limit_state.read_mode.value == "command_cache"
    l30_current = l30.telemetry_channel(TelemetrySemantic.CURRENTS)
    assert l30_current is not None
    assert l30_current.raw_unit == "count"
    assert "6.5 mA/count" in (l30_current.raw_scale_note or "")


def test_l30_current_channels_expose_protocol_counts_not_milliamps() -> None:
    """The protocol documents "范围：60～800，单位 6.5ma", so raw values are counts.

    Declaring these as mA would tell a caller that writing 500 limits the joint
    to 500 mA when the device actually applies 3250 mA. The scale belongs in
    ``raw_scale_note`` only; the SDK never converts it.
    """

    l30 = get_declared_capabilities("l30")
    channels = (
        l30.command(CommandSemantic.CURRENT_LIMITS),
        l30.telemetry_channel(TelemetrySemantic.CURRENT_LIMITS),
        l30.telemetry_channel(TelemetrySemantic.CURRENTS),
    )

    for channel in channels:
        assert channel is not None
        assert channel.raw_unit == "count"
        assert "6.5 mA/count" in (channel.raw_scale_note or "")
        # ``MEASURED`` would claim the unit was confirmed on hardware; the only
        # evidence here is the protocol document.
        assert channel.unit_confidence is Confidence.DOCUMENTED


def test_o30_stall_contract_maps_only_installed_channels() -> None:
    capabilities = get_declared_capabilities("o30", side="right")
    stall = capabilities.stall_protection

    assert stall is not None
    assert stall.channels == capabilities.joint_names
    assert len(stall.channels) == 20
    assert not stall.safety_range_verified
    assert stall.detection_time.raw_unit is None
    assert capabilities.operations.read_stall_protection
    assert capabilities.operations.write_stall_protection
    assert stall.detection_time.raw_encoding == "u8_v25_object"
    assert stall.threshold.raw_encoding == "u8_v25_object"
    assert stall.hold_current.raw_encoding == "u8_v25_object"


def test_o30_capability_declares_compact_hop_transport_contract() -> None:
    capabilities = get_declared_capabilities("o30", side="left")

    assert all(
        command.raw_encoding == "u8_v25_object" for command in capabilities.commands
    )
    assert capabilities.transport.nominal_bitrate == 1_000_000
    assert capabilities.transport.data_bitrate is None
    assert "0x001" in capabilities.transport.addressing_note
    assert "0x401" in capabilities.transport.addressing_note
    assert "BRS disabled" in capabilities.transport.addressing_note


def test_o30_writes_declare_matchable_hop_response_ack() -> None:
    capabilities = get_declared_capabilities("o30", side="right")

    assert capabilities.commands
    assert all(
        command.ack_support is AckSupport.SUPPORTED for command in capabilities.commands
    )


def test_o6_declared_sensor_shape_matches_the_assembled_layout() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)

    assert force is not None
    assert force.shape == (5, 10, 4)
    assert force.unit_confidence is Confidence.UNKNOWN


def test_o6_latest_protocol_metadata_exposes_units_and_stall_management() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    stall = capabilities.stall_protection

    assert stall is not None
    assert stall.channels == capabilities.joint_names
    assert stall.read_supported and stall.write_supported
    assert stall.detection_time.raw_scale_note == "10 ms/count"
    assert "1000/255 mA/count" in (stall.threshold.raw_scale_note or "")
    assert stall.threshold.unit_confidence is Confidence.DOCUMENTED
    assert "exposed as hold current" in (stall.hold_current.raw_scale_note or "")
    assert stall.hold_current.unit_confidence is Confidence.CONFLICTING
    assert capabilities.operations.save_parameters
    assert capabilities.operations.read_stall_protection
    assert capabilities.operations.write_stall_protection
    assert "save_parameters" in capabilities.operations.acknowledged_operations

    speed = capabilities.command(CommandSemantic.SPEEDS)
    acceleration = capabilities.command(CommandSemantic.ACCELERATIONS)
    current_limit = capabilities.command(CommandSemantic.CURRENT_LIMITS)
    faults = capabilities.telemetry_channel(TelemetrySemantic.FAULTS)
    temperature = capabilities.telemetry_channel(TelemetrySemantic.TEMPERATURES)
    assert speed is not None and "0.732 rpm/count" in (speed.raw_scale_note or "")
    assert acceleration is not None and "8.7" in (acceleration.raw_scale_note or "")
    assert current_limit is not None and "6.5 mA/count" in (
        current_limit.raw_scale_note or ""
    )
    assert all(
        command.ack_support is AckSupport.SUPPORTED
        for command in (speed, acceleration, current_limit)
    )
    assert faults is not None and "bit5 load overload" in (faults.raw_scale_note or "")
    assert temperature is not None and temperature.raw_unit == "degree_celsius"


def test_o6_acceleration_excludes_protocol_byte_ff() -> None:
    capabilities = get_declared_capabilities("o6", side="right")
    acceleration = capabilities.command(CommandSemantic.ACCELERATIONS)

    assert acceleration is not None
    assert acceleration.range_for(0).contains(254)
    assert not acceleration.range_for(0).contains(255)


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


def test_l6_latest_protocol_metadata_exposes_units_and_management_objects() -> None:
    capabilities = get_declared_capabilities("l6", side="left")
    stall = capabilities.stall_protection

    assert stall is not None
    assert stall.read_supported and stall.write_supported
    assert stall.detection_time.raw_scale_note is not None
    assert "10 ms/count" in stall.detection_time.raw_scale_note
    assert "1400/255 protocol-units/count" in (stall.threshold.raw_scale_note or "")
    assert stall.threshold.unit_confidence is Confidence.UNKNOWN
    assert "stall torque" in (stall.hold_current.raw_scale_note or "")
    assert stall.hold_current.unit_confidence is Confidence.CONFLICTING
    assert capabilities.command(CommandSemantic.LIMIT_COMPENSATIONS) is not None
    assert (
        capabilities.telemetry_channel(TelemetrySemantic.LIMIT_COMPENSATIONS)
        is not None
    )
    assert capabilities.operations.save_parameters
    assert "save_parameters" in capabilities.operations.acknowledged_operations

    currents = capabilities.telemetry_channel(TelemetrySemantic.CURRENTS)
    temperatures = capabilities.telemetry_channel(TelemetrySemantic.TEMPERATURES)
    faults = capabilities.telemetry_channel(TelemetrySemantic.FAULTS)
    force = capabilities.telemetry_channel(TelemetrySemantic.FORCE)
    assert currents is not None
    assert "absolute current magnitude" in (currents.raw_scale_note or "")
    assert temperatures is not None and temperatures.raw_unit == "degree_celsius"
    assert faults is not None and "bit6 MCU" in (faults.raw_scale_note or "")
    assert force is not None and force.unit_confidence is Confidence.UNKNOWN


def test_runtime_disabled_telemetry_retains_metadata_but_reports_unsupported() -> None:
    declared = get_declared_capabilities("l6", side="left")
    degraded = replace(
        declared,
        firmware_unsupported_telemetry=(TelemetrySemantic.CURRENTS,),
    )

    assert degraded.telemetry_channel(TelemetrySemantic.CURRENTS) is not None
    assert not degraded.telemetry_supported(TelemetrySemantic.CURRENTS)
    assert degraded.telemetry_supported(TelemetrySemantic.ANGLES)

    with pytest.raises(ValidationError, match="must remain declared"):
        replace(
            declared,
            firmware_unsupported_telemetry=(TelemetrySemantic.VOLTAGES,),
        )


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
