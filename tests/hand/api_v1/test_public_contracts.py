from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import linkerbot_hand
import linkerbot_hand.api as hand_api
import linkerbot_hand.capabilities as hand_capabilities
import linkerbot_hand.discovery as hand_discovery
from linkerbot_hand.api.commands import (
    AckSupport,
    CommandFamily,
    CommandHandle,
    CommandStage,
)
from linkerbot_hand.api.config import DeviceConfig, PollingPlan
from linkerbot_hand.api.device_info import DeviceInfo, VersionField
from linkerbot_hand.api.enums import AngleFeedbackPolicy, DataOrigin, HandSide
from linkerbot_hand.api.errors import (
    ErrorCode,
    ErrorInfo,
    IdentityMismatchError,
    LifecycleError,
    PollingPlanError,
    ValidationError,
)
from linkerbot_hand.api.health import ConnectionState, HealthStatus, LifecycleState
from linkerbot_hand.api.readings import AngleSample, CurrentSample, SampleMeta
from linkerbot_hand.api.snapshot import HandSnapshot, JointState
from linkerbot_hand.capabilities.registry import get_declared_capabilities
from linkerbot_hand.capabilities.types import CommandSemantic


def valid_meta(*, origin: DataOrigin = DataOrigin.DEVICE_RESPONSE) -> SampleMeta:
    return SampleMeta(
        sequence=1,
        collected_at=10.0,
        age_s=0.02,
        valid=True,
        stale=False,
        connection_generation=3,
        origin=origin,
        within_declared_range=True,
    )


def test_public_contract_exports_resolve() -> None:
    assert hand_api.__all__
    assert all(getattr(hand_api, name) is not None for name in hand_api.__all__)


def test_top_level_exports_exactly_the_public_contract_packages() -> None:
    expected = (
        set(hand_api.__all__)
        | set(hand_capabilities.__all__)
        | set(hand_discovery.__all__)
    )

    assert set(linkerbot_hand.__all__) == expected
    assert all(getattr(linkerbot_hand, name) is not None for name in expected)
    assert linkerbot_hand.HandDevice is hand_api.HandDevice


@pytest.mark.parametrize(
    "removed_name",
    (
        "HandSession",
        "LegacyHandBackend",
        "L6",
        "O6",
        "L20lite",
        "L20",
        "O20",
        "L30",
        "O30i",
        "create_hand_device",
    ),
)
def test_removed_facades_are_absent_from_the_top_level(removed_name: str) -> None:
    assert removed_name not in linkerbot_hand.__all__
    assert not hasattr(linkerbot_hand, removed_name)


def test_polling_plan_uses_zero_to_disable_a_channel() -> None:
    plan = PollingPlan(angles_hz=100, force_hz=30, temperature_hz=0)

    assert plan.enabled_rates() == (("angles", 100.0), ("force", 30.0))


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "1"])
def test_polling_plan_rejects_invalid_rates(value: object) -> None:
    with pytest.raises(ValidationError):
        PollingPlan(angles_hz=value)  # ty: ignore[invalid-argument-type]


def test_polling_plan_has_one_typed_angle_feedback_policy() -> None:
    plan = PollingPlan(angles_hz=100)

    assert plan.angle_feedback is AngleFeedbackPolicy.ADAPTIVE
    assert plan.enabled_rates() == (("angles", 100.0),)
    with pytest.raises(ValidationError, match="AngleFeedbackPolicy"):
        PollingPlan(angle_feedback="adaptive")  # ty: ignore[invalid-argument-type]


def test_angle_command_declares_only_verified_current_state_responses() -> None:
    response_profiles = {
        ("l6", "left"),
        ("o6", "right"),
        ("l20lite", "left"),
        ("l20", "right"),
    }
    dispatch_or_ack_profiles = {
        ("o20", "right"),
        ("l30", None),
        ("o30i", "right"),
    }

    for model, side in response_profiles:
        capabilities = get_declared_capabilities(model, side=side)
        angles = capabilities.command(CommandSemantic.ANGLES)
        assert angles is not None and angles.returns_current_state
    for model, side in dispatch_or_ack_profiles:
        capabilities = get_declared_capabilities(model, side=side)
        angles = capabilities.command(CommandSemantic.ANGLES)
        assert angles is not None and not angles.returns_current_state


def test_device_config_rejects_unbounded_or_invalid_limits() -> None:
    with pytest.raises(ValidationError):
        DeviceConfig(discrete_queue_capacity=0)
    with pytest.raises(ValidationError):
        DeviceConfig(shutdown_timeout_s=0)


def test_sample_contracts_are_immutable_and_keep_all_angle_representations() -> None:
    sample = AngleSample(
        raw=(0, 127, 255),
        percent=(0.0, 49.8, 100.0),
        physical_rad=(None, None, None),
        meta=valid_meta(),
    )

    assert sample.raw == (0, 127, 255)
    assert sample.percent == (0.0, 49.8, 100.0)
    with pytest.raises(FrozenInstanceError):
        sample.raw = (1, 2, 3)  # ty: ignore[invalid-assignment]


def test_angle_sample_rejects_mismatched_representations() -> None:
    with pytest.raises(ValidationError):
        AngleSample(
            raw=(0, 255),
            percent=(0.0,),
            physical_rad=(None, None),
            meta=valid_meta(),
        )


def test_invalid_sample_cannot_claim_collection_metadata() -> None:
    with pytest.raises(ValidationError):
        SampleMeta(
            sequence=0,
            collected_at=10.0,
            age_s=None,
            valid=False,
            stale=True,
            connection_generation=0,
            origin=DataOrigin.NONE,
            within_declared_range=True,
        )


def test_device_info_preserves_raw_and_structured_versions() -> None:
    info = DeviceInfo(
        model="O6",
        serial_number="730",
        pcb_version=VersionField(raw=(4, 1), text="04.1", major=4, minor=1),
        firmware_version=None,
        mechanical_version=VersionField(raw=b"D", text="D"),
        product_code="LHO6-04.1-730-R-B-1-D",
        sensor_variant="B",
        side=HandSide.RIGHT,
        raw_identity=b"LHO6-04.1-730-R-B-1-D",
        meta=valid_meta(),
    )

    assert info.sensor_variant == "B"
    assert info.pcb_version is not None
    assert info.pcb_version.text == "04.1"


def test_snapshot_is_an_immutable_atomic_view() -> None:
    angles = AngleSample(
        raw=(0,),
        percent=(0.0,),
        physical_rad=(None,),
        meta=valid_meta(),
    )
    snapshot = HandSnapshot(
        captured_at=12.0,
        snapshot_sequence=4,
        connection_generation=3,
        joints=(JointState(index=0, name="thumb", angle_raw=0, angle_percent=0.0),),
        angles=angles,
        currents=CurrentSample(raw=(7,), meta=valid_meta()),
    )

    assert snapshot.angles is angles
    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_sequence = 5  # ty: ignore[invalid-assignment]


def test_structured_hand_error_keeps_stable_context() -> None:
    cause = RuntimeError("identity changed")
    error = IdentityMismatchError(
        "connected device does not match",
        operation="open",
        model="L6",
        generation=2,
        cause=cause,
    )

    assert error.code is ErrorCode.IDENTITY_MISMATCH
    assert error.cause is cause
    assert ErrorInfo.from_exception(error) == ErrorInfo(
        code=ErrorCode.IDENTITY_MISMATCH,
        message="connected device does not match",
        error_type="IdentityMismatchError",
        operation="open",
        model="L6",
        generation=2,
    )
    assert PollingPlanError.code is ErrorCode.POLLING_PLAN


def test_command_handle_distinguishes_dispatch_from_device_ack() -> None:
    handle = CommandHandle(
        command_id=7,
        family=CommandFamily.ANGLE_TARGET,
        ack_support=AckSupport.SUPPORTED,
        accepted_at=1.0,
    )

    handle._mark_dispatched(timestamp=1.1, sent_frames=2)
    assert handle.stage is CommandStage.DISPATCHED
    assert not handle.done
    handle._mark_acknowledged(timestamp=1.2)
    assert handle.wait(timeout=0.01) is CommandStage.ACKNOWLEDGED
    assert handle.sent_frames == 2


def test_dispatched_is_terminal_when_ack_is_unsupported() -> None:
    handle = CommandHandle(
        command_id=8,
        family=CommandFamily.SPEED_SETTING,
        ack_support=AckSupport.UNSUPPORTED,
        accepted_at=1.0,
    )

    handle._mark_dispatched(timestamp=1.1, sent_frames=1)

    assert handle.done
    assert handle.wait(timeout=0.01) is CommandStage.DISPATCHED
    with pytest.raises(LifecycleError, match="already completed"):
        handle._mark_failed(RuntimeError("late failure"), timestamp=1.2, sent_frames=1)
    assert handle.stage is CommandStage.DISPATCHED


def test_dispatched_requires_actual_frames_and_monotonic_time() -> None:
    handle = CommandHandle(
        command_id=9,
        family=CommandFamily.ANGLE_TARGET,
        ack_support=AckSupport.SUPPORTED,
        accepted_at=2.0,
    )

    with pytest.raises(ValidationError, match="at least one frame"):
        handle._mark_dispatched(timestamp=2.1, sent_frames=0)
    with pytest.raises(LifecycleError, match="backwards"):
        handle._mark_dispatched(timestamp=1.9, sent_frames=1)

    assert handle.stage is CommandStage.ACCEPTED


def test_post_dispatch_failure_preserves_sent_frame_count() -> None:
    handle = CommandHandle(
        command_id=10,
        family=CommandFamily.ANGLE_TARGET,
        ack_support=AckSupport.SUPPORTED,
        accepted_at=3.0,
    )
    handle._mark_dispatched(timestamp=3.1, sent_frames=2)

    with pytest.raises(LifecycleError, match="frame count"):
        handle._mark_failed(RuntimeError("timeout"), timestamp=3.2, sent_frames=0)

    assert handle.stage is CommandStage.DISPATCHED
    assert handle.sent_frames == 2


def test_health_contract_is_immutable_and_zero_state_is_explicit() -> None:
    health = HealthStatus.initial()

    assert health.lifecycle_state is LifecycleState.CLOSED
    assert health.connection_state is ConnectionState.CLOSED
    assert not health.connected
    assert not health.polling_active
    with pytest.raises(FrozenInstanceError):
        health.connected = True  # ty: ignore[invalid-assignment]
