from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest

from linkerbot_hand import (
    AckSupport,
    CommandHandle,
    CommandKind,
    DeviceCapabilities,
    DeviceFeatures,
    DeviceIdentity,
    HandCommand,
    HandSide,
    JointCapability,
    JointEndpoint,
    JointMotionType,
    NumericRange,
    PercentageMapping,
    PercentageSemantics,
    PollingPlan,
    SensorCapability,
    SensorType,
    SessionConfig,
    TransportType,
    ValidationError,
    get_model_capabilities,
)
from linkerbot_hand.capabilities.builders import classic_joints

from ._support import make_rig


@pytest.mark.parametrize("side", [1, True, object()])
def test_capability_factory_rejects_invalid_side_types(side: object) -> None:
    with pytest.raises(ValidationError, match="side must be HandSide or str"):
        get_model_capabilities("L6", side=cast(Any, side))


@pytest.mark.parametrize("firmware", [1, True, object()])
def test_capability_factory_rejects_invalid_firmware_types(firmware: object) -> None:
    with pytest.raises(ValidationError, match="firmware must be str or None"):
        get_model_capabilities("L6", firmware=cast(Any, firmware))


@pytest.mark.parametrize("transport", ["can", "serial", 1, object()])
def test_capability_factory_rejects_invalid_transport_types(transport: object) -> None:
    with pytest.raises(
        ValidationError, match="transport must be TransportType or None"
    ):
        get_model_capabilities("L6", transport=cast(Any, transport))


def test_capability_factory_accepts_documented_string_side_and_enum_transport() -> None:
    capabilities = get_model_capabilities(
        "l6",
        side="RIGHT",
        firmware="1.2.3",
        transport=TransportType.CUSTOM,
    )

    assert capabilities.side is HandSide.RIGHT
    assert capabilities.firmware == "1.2.3"
    assert capabilities.transport is TransportType.CUSTOM


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("minimum", "0", "range minimum must be numeric"),
        ("maximum", True, "range maximum must be numeric"),
        ("unit", 1, "range unit must be a non-empty str"),
        ("integer_only", 1, "range integer_only must be bool"),
        ("step", True, "range step must be numeric"),
    ],
)
def test_numeric_range_rejects_malformed_field_types(
    field: str, value: object, message: str
) -> None:
    values: dict[str, Any] = {
        "minimum": 0,
        "maximum": 255,
        "unit": "raw_u8",
        "integer_only": True,
        "step": 1,
    }
    values[field] = value

    with pytest.raises(ValidationError, match=message):
        NumericRange(**values)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: NumericRange(10**400, 10**400, "raw"),
        lambda: NumericRange(0, 1, "raw", step=10**400),
        lambda: SensorCapability(SensorType.ANGLES, 10**400, 1),
        lambda: PollingPlan(angles_hz=10**400),
        lambda: SessionConfig(command_rate_hz=10**400),
    ],
)
def test_public_numeric_types_reject_unrepresentable_integers(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError, match="finite"):
        factory()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"raw_open": 255.0}, "percentage raw endpoints must be int"),
        ({"raw_closed": True}, "percentage raw endpoints must be int"),
        ({"physical_angle": object()}, "physical_angle must be NumericRange or None"),
        ({"semantics": "openness"}, "percentage semantics must be"),
        ({"zero_endpoint": "flexed"}, "zero_endpoint must be JointEndpoint"),
        ({"hundred_endpoint": "extended"}, "hundred_endpoint must be JointEndpoint"),
    ],
)
def test_percentage_mapping_rejects_malformed_field_types(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, Any] = {
        "raw_open": 255,
        "raw_closed": 0,
        "semantics": PercentageSemantics.OPENNESS,
    }
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        PercentageMapping(**values)


def test_percentage_mapping_rejects_invalid_endpoint_semantics() -> None:
    with pytest.raises(ValidationError, match="raw endpoints must be distinct"):
        PercentageMapping(raw_open=128, raw_closed=128)

    with pytest.raises(ValidationError, match="distinct poses"):
        PercentageMapping(
            raw_open=255,
            raw_closed=0,
            semantics=PercentageSemantics.POSITION,
            zero_endpoint=JointEndpoint.TOWARD_THUMB,
            hundred_endpoint=JointEndpoint.TOWARD_THUMB,
        )

    with pytest.raises(ValidationError, match="openness mapping must use"):
        PercentageMapping(
            raw_open=255,
            raw_closed=0,
            zero_endpoint=JointEndpoint.AWAY_FROM_THUMB,
            hundred_endpoint=JointEndpoint.TOWARD_THUMB,
        )


def test_joint_capability_rejects_mapping_endpoints_outside_raw_range() -> None:
    with pytest.raises(ValidationError, match="raw_open must be within"):
        JointCapability(
            index=0,
            name="joint",
            raw_angle_range=NumericRange(0, 100, "raw"),
            percentage_mapping=PercentageMapping(raw_open=101, raw_closed=0),
        )


def test_joint_capability_requires_raw_range_for_percentage_mapping() -> None:
    with pytest.raises(ValidationError, match="requires a verified raw_angle_range"):
        JointCapability(
            index=0,
            name="joint",
            raw_angle_range=None,
            percentage_mapping=PercentageMapping(raw_open=100, raw_closed=0),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"motion_type": "flexion"}, "motion_type must be JointMotionType"),
        ({"coupled_with": ["other"]}, "coupled_with must be a tuple"),
        ({"coupled_with": ("joint",)}, "joint cannot be coupled with itself"),
        ({"coupled_with": ("other", "other")}, "coupled_with values must be unique"),
    ],
)
def test_joint_capability_rejects_invalid_motion_metadata(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, Any] = {
        "index": 0,
        "name": "joint",
        "raw_angle_range": NumericRange(0, 255, "raw"),
        "percentage_mapping": PercentageMapping(raw_open=255, raw_closed=0),
        "motion_type": JointMotionType.FLEXION,
    }
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        JointCapability(**values)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            (("joint",), (), set()),
            "names, endpoints, and motion types must have equal lengths",
        ),
        (
            (("joint",), ((255, 0),), {1}),
            "directional axis index is outside",
        ),
        (
            (("joint",), ((255, 0),), {0}),
            "direction endpoints must be declared",
        ),
    ],
)
def test_classic_joint_builder_rejects_incomplete_model_declarations(
    args: tuple[object, object, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        classic_joints(*cast(Any, args))


@pytest.mark.parametrize(
    ("sensor", "recommended_hz", "stale_after_s", "message"),
    [
        ("angles", 60, 0.05, "sensor must be SensorType"),
        (SensorType.ANGLES, True, 0.05, "recommended_hz must be numeric"),
        (SensorType.ANGLES, 60, "0.05", "stale_after_s must be numeric"),
    ],
)
def test_sensor_capability_rejects_malformed_field_types(
    sensor: object, recommended_hz: object, stale_after_s: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        SensorCapability(
            sensor=cast(Any, sensor),
            recommended_hz=cast(Any, recommended_hz),
            stale_after_s=cast(Any, stale_after_s),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"device_ack": 1}, "device_ack must be bool"),
        ({"clear_faults": None}, "clear_faults must be bool"),
        ({"acknowledged_commands": [CommandKind.ANGLES]}, "must be a tuple"),
        ({"acknowledged_commands": ("angles",)}, "must be a tuple"),
    ],
)
def test_device_features_rejects_malformed_field_types(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, Any] = {}
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        DeviceFeatures(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", 6, "model must be a non-empty str"),
        ("firmware", 123, "firmware must be str or None"),
        ("side", "left", "side must be HandSide"),
        ("joints", [], "joints must be a tuple"),
        ("sensors", [], "sensors must be a tuple"),
        ("transport", "can", "transport must be TransportType"),
        ("features", object(), "features must be DeviceFeatures"),
        ("percentage_convention", "0=open", "percentage_convention must be"),
    ],
)
def test_device_capabilities_rejects_malformed_field_types(
    field: str, value: object, message: str
) -> None:
    valid = get_model_capabilities("L6")

    with pytest.raises(ValidationError, match=message):
        replace(valid, **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", 6, "identity model must be a non-empty str"),
        ("firmware", 123, "identity firmware must be str or None"),
        ("side", "right", "identity side must be HandSide"),
        ("serial_number", 123, "serial_number must be str or None"),
    ],
)
def test_device_identity_rejects_malformed_field_types(
    field: str, value: object, message: str
) -> None:
    values: dict[str, Any] = {"model": "L6"}
    values[field] = value

    with pytest.raises(ValidationError, match=message):
        DeviceIdentity(**values)


@pytest.mark.parametrize("auto_reconnect", [0, 1, None, "yes"])
def test_session_config_requires_boolean_auto_reconnect(
    auto_reconnect: object,
) -> None:
    with pytest.raises(ValidationError, match="auto_reconnect must be bool"):
        SessionConfig(auto_reconnect=cast(Any, auto_reconnect))


def test_capabilities_with_identity_rejects_wrong_object_type() -> None:
    capabilities: DeviceCapabilities = get_model_capabilities("L6")

    with pytest.raises(ValidationError, match="identity must be DeviceIdentity"):
        capabilities.with_identity(cast(Any, object()))


@pytest.mark.parametrize(
    "timeout", [True, "1", -0.1, float("nan"), float("inf"), 10**400]
)
def test_recover_rejects_invalid_timeout_with_stable_error(timeout: object) -> None:
    rig = make_rig()

    with pytest.raises(
        ValidationError, match="timeout must be finite and non-negative"
    ):
        rig.device.recover(cast(Any, timeout))


@pytest.mark.parametrize(
    "timeout", [True, "1", -0.1, float("nan"), float("inf"), 10**400]
)
def test_command_handle_wait_rejects_invalid_timeout_with_stable_error(
    timeout: object,
) -> None:
    handle = CommandHandle(
        1,
        HandCommand(CommandKind.ANGLES, ()),
        ack_support=AckSupport.UNSUPPORTED,
        accepted_at=0.0,
    )

    with pytest.raises(
        ValidationError, match="timeout must be finite and non-negative"
    ):
        handle.wait(cast(Any, timeout))


@pytest.mark.parametrize(
    "method_name",
    [
        "submit_angles",
        "submit_raw_angles",
        "submit_speeds",
        "submit_torques",
    ],
)
def test_command_submission_rejects_unrepresentable_integers(
    method_name: str,
) -> None:
    rig = make_rig()
    rig.device.open()
    try:
        method = getattr(rig.device, method_name)
        with pytest.raises(ValidationError, match="must be finite"):
            method((10**400,) * rig.device.get_capabilities().joint_count)
    finally:
        rig.device.close()


@pytest.mark.parametrize(
    "method_name",
    [
        "percent_to_raw",
        "raw_to_percent",
        "raw_to_physical_angles",
        "physical_angles_to_raw",
    ],
)
def test_angle_conversions_reject_unrepresentable_integers(
    method_name: str,
) -> None:
    rig = make_rig("O20")
    method = getattr(rig.device, method_name)

    with pytest.raises(ValidationError, match="must be finite"):
        method((10**400,) * rig.device.get_capabilities().joint_count)
