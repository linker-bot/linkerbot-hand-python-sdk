"""Copy-on-write canonical state store for Hand SDK 1.0."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..api._force import merge_force_samples
from ..api._initial_state import make_initial_snapshot
from ..api.device_info import DeviceInfo
from ..api.enums import DataOrigin
from ..api.errors import ProtocolError, ValidationError
from ..api.health import DataAge
from ..api.readings import AngleSample, ForceSample, SampleMeta, _RawVectorSample
from ..api.snapshot import HandSnapshot
from ..capabilities.force import FORCE_QUERIES
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..protocols.base import SamplePayload
from ._snapshot_samples import (
    SAMPLE_ATTRIBUTES,
    JointProjection,
    age_device_info,
    age_sample,
    build_sample,
    invalidate_snapshot,
    payload_within_range,
    snapshot_with_generation,
    validate_payload_shape,
)


@dataclass(slots=True)
class _GenerationState:
    capabilities: DeviceCapabilities
    generation: int
    snapshot: HandSnapshot
    collected_monotonic: dict[TelemetrySemantic, float]
    device_info_collected_monotonic: float | None
    sequences: dict[TelemetrySemantic, int]
    device_info_sequence: int
    last_change: HandSnapshot
    last_change_collected_monotonic: dict[TelemetrySemantic, float]
    last_change_device_info_collected_monotonic: float | None
    last_non_command_feedback_change: HandSnapshot
    last_non_command_feedback_collected_monotonic: dict[TelemetrySemantic, float]
    last_non_command_feedback_device_info_collected_monotonic: float | None


class SnapshotStore:
    """Publish immutable channel samples and build bounded zero-I/O views."""

    def __init__(
        self,
        capabilities: DeviceCapabilities,
        initial_snapshot: HandSnapshot,
    ) -> None:
        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if not isinstance(initial_snapshot, HandSnapshot):
            raise ValidationError("initial_snapshot must be HandSnapshot")
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._changed_without_command_feedback = threading.Condition(self._lock)
        self._current = _generation_state(capabilities, initial_snapshot)
        self._candidate: _GenerationState | None = None
        self._joint_projection = JointProjection()

    def begin_generation(
        self,
        capabilities: DeviceCapabilities,
        *,
        generation: int,
        captured_at: float,
    ) -> None:
        """Create a hidden candidate generation for identity and baseline data."""

        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if type(generation) is not int or generation <= 0:
            raise ValidationError("generation must be a positive int")
        _validate_time(captured_at)
        initial = make_initial_snapshot(capabilities)
        invalid = snapshot_with_generation(initial, generation, captured_at)
        with self._lock:
            if self._candidate is not None:
                raise ProtocolError("a snapshot generation is already being prepared")
            if generation <= self._current.generation:
                raise ProtocolError("candidate generation must advance monotonically")
            self._candidate = _generation_state(capabilities, invalid)

    def commit_generation(self, *, generation: int) -> None:
        """Atomically publish one fully verified candidate generation."""

        with self._lock:
            candidate = self._candidate
            if candidate is None or candidate.generation != generation:
                raise ProtocolError("snapshot candidate generation is unavailable")
            self._current = candidate
            self._candidate = None
            _record_visible_change(candidate, command_feedback=False)
            self._changed.notify_all()
            self._changed_without_command_feedback.notify_all()

    def reset_invalid(
        self,
        capabilities: DeviceCapabilities,
        *,
        generation: int,
        captured_at: float,
    ) -> None:
        """Roll back a failed candidate to one fully invalid cached generation."""

        if not isinstance(capabilities, DeviceCapabilities):
            raise ValidationError("capabilities must be DeviceCapabilities")
        if type(generation) is not int or generation < 0:
            raise ValidationError("generation must be a non-negative int")
        _validate_time(captured_at)
        initial = make_initial_snapshot(capabilities)
        invalid = snapshot_with_generation(initial, generation, captured_at)
        with self._lock:
            self._current = _generation_state(capabilities, invalid)
            self._candidate = None
            self._changed.notify_all()
            self._changed_without_command_feedback.notify_all()

    def observe_force_capabilities(
        self, capabilities: DeviceCapabilities, *, generation: int
    ) -> None:
        """Expose support changes without inventing a new sensor acquisition."""
        with self._lock:
            state = self._state_for_generation_locked(generation)
            if state.capabilities is capabilities:
                return
            state.capabilities = capabilities
            force = state.snapshot.force
            if force is None:
                return
            available = {field.name for field in force.fields}
            unsupported = tuple(
                field.name
                for field in capabilities.force_fields
                if field.supported is False and field.name not in available
            )
            if force.unsupported_fields == unsupported:
                return
            state.snapshot = replace(
                state.snapshot,
                snapshot_sequence=state.snapshot.snapshot_sequence + 1,
                force=replace(force, unsupported_fields=unsupported),
            )
            _record_visible_change(state, command_feedback=False)
            if state is self._current:
                self._changed.notify_all()
                self._changed_without_command_feedback.notify_all()

    def publish(
        self,
        payload: SamplePayload,
        *,
        generation: int,
        collected_at: float,
        collected_monotonic: float,
    ) -> AngleSample | ForceSample | _RawVectorSample:
        """Validate, sequence, and atomically publish one decoded payload."""

        if not isinstance(payload, SamplePayload):
            raise ValidationError("payload must be SamplePayload")
        _validate_time(collected_at)
        _validate_time(collected_monotonic)
        with self._lock:
            state = self._state_for_generation_locked(generation)
            telemetry = state.capabilities.telemetry_channel(payload.semantic)
            if telemetry is None:
                raise ProtocolError(
                    f"profile has no {payload.semantic.value} telemetry"
                )
            validate_payload_shape(payload, state.capabilities)
            sequence = state.sequences[payload.semantic] + 1
            meta = SampleMeta(
                sequence=sequence,
                collected_at=collected_at,
                age_s=0.0,
                valid=True,
                stale=False,
                connection_generation=generation,
                origin=payload.origin,
                within_declared_range=payload_within_range(payload, state.capabilities),
            )
            sample = build_sample(payload, meta, state.capabilities)
            attribute = SAMPLE_ATTRIBUTES[payload.semantic]
            cached = (
                merge_force_samples(state.snapshot.force, sample)
                if isinstance(sample, ForceSample)
                else sample
            )
            state.snapshot = replace(
                state.snapshot,
                captured_at=collected_at,
                snapshot_sequence=state.snapshot.snapshot_sequence + 1,
                **{attribute: cached},
            )
            state.sequences[payload.semantic] = sequence
            state.collected_monotonic[payload.semantic] = collected_monotonic
            command_feedback = payload.origin is DataOrigin.DEVICE_COMMAND_FEEDBACK
            _record_visible_change(state, command_feedback=command_feedback)
            if state is self._current:
                self._changed.notify_all()
                if not command_feedback:
                    self._changed_without_command_feedback.notify_all()
            return sample

    def publish_device_info(
        self,
        device_info: DeviceInfo,
        *,
        generation: int,
        collected_at: float,
        collected_monotonic: float,
    ) -> DeviceInfo:
        """Publish one verified identity sample in the current generation."""

        if not isinstance(device_info, DeviceInfo):
            raise ValidationError("device_info must be DeviceInfo")
        _validate_time(collected_at)
        _validate_time(collected_monotonic)
        with self._lock:
            state = self._state_for_generation_locked(generation)
            if (
                device_info.model != state.capabilities.model
                or device_info.side is not state.capabilities.side
            ):
                raise ProtocolError("device info does not match verified capability")
            state.device_info_sequence += 1
            published = replace(
                device_info,
                meta=SampleMeta(
                    sequence=state.device_info_sequence,
                    collected_at=collected_at,
                    age_s=0.0,
                    valid=True,
                    stale=False,
                    connection_generation=generation,
                    origin=DataOrigin.DEVICE_RESPONSE,
                    within_declared_range=True,
                ),
            )
            state.snapshot = replace(
                state.snapshot,
                captured_at=collected_at,
                snapshot_sequence=state.snapshot.snapshot_sequence + 1,
                device_info=published,
            )
            state.device_info_collected_monotonic = collected_monotonic
            _record_visible_change(state, command_feedback=False)
            if state is self._current:
                self._changed.notify_all()
                self._changed_without_command_feedback.notify_all()
            return published

    def invalidate(self, *, generation: int, captured_at: float) -> None:
        """Invalidate every sample from a lost connection without changing generation."""

        _validate_time(captured_at)
        with self._lock:
            if generation != self._current.generation:
                raise ProtocolError("sample belongs to a stale connection generation")
            self._current.snapshot = invalidate_snapshot(
                self._current.snapshot,
                generation=generation,
                captured_at=captured_at,
            )
            self._current.collected_monotonic.clear()
            self._current.device_info_collected_monotonic = None
            _record_visible_change(self._current, command_feedback=False)
            self._candidate = None
            self._changed.notify_all()
            self._changed_without_command_feedback.notify_all()

    def wait_for_update(
        self,
        *,
        connection_generation: int,
        snapshot_sequence: int,
        timeout_s: float,
        wake_on_command_feedback: bool = True,
    ) -> bool:
        """Wait boundedly for a visible snapshot revision, without device I/O."""

        _validate_wait_arguments(
            connection_generation=connection_generation,
            snapshot_sequence=snapshot_sequence,
            timeout_s=timeout_s,
            wake_on_command_feedback=wake_on_command_feedback,
        )
        condition = (
            self._changed
            if wake_on_command_feedback
            else self._changed_without_command_feedback
        )
        with condition:
            return condition.wait_for(
                lambda: self._has_visible_update_locked(
                    connection_generation=connection_generation,
                    snapshot_sequence=snapshot_sequence,
                    wake_on_command_feedback=wake_on_command_feedback,
                ),
                timeout=float(timeout_s),
            )

    def wait_for_aged_change(
        self,
        *,
        connection_generation: int,
        snapshot_sequence: int,
        timeout_s: float,
        captured_at: Callable[[], float],
        monotonic_now: Callable[[], float],
        wake_on_command_feedback: bool = True,
    ) -> HandSnapshot | None:
        """Return one exact revision with coherent current age and stale flags."""

        _validate_wait_arguments(
            connection_generation=connection_generation,
            snapshot_sequence=snapshot_sequence,
            timeout_s=timeout_s,
            wake_on_command_feedback=wake_on_command_feedback,
        )
        if not callable(captured_at):
            raise ValidationError("captured_at must be callable")
        if not callable(monotonic_now):
            raise ValidationError("monotonic_now must be callable")
        condition = (
            self._changed
            if wake_on_command_feedback
            else self._changed_without_command_feedback
        )
        with condition:
            changed = condition.wait_for(
                lambda: self._has_visible_update_locked(
                    connection_generation=connection_generation,
                    snapshot_sequence=snapshot_sequence,
                    wake_on_command_feedback=wake_on_command_feedback,
                ),
                timeout=float(timeout_s),
            )
            if not changed:
                return None
            state = self._current
            if wake_on_command_feedback:
                snapshot = state.last_change
                collected_monotonic = state.last_change_collected_monotonic
                device_info_collected_monotonic = (
                    state.last_change_device_info_collected_monotonic
                )
            else:
                snapshot = state.last_non_command_feedback_change
                collected_monotonic = (
                    state.last_non_command_feedback_collected_monotonic
                )
                device_info_collected_monotonic = (
                    state.last_non_command_feedback_device_info_collected_monotonic
                )
            capabilities = state.capabilities
            generation = state.generation
        return _build_view(
            snapshot,
            joint_projection=self._joint_projection,
            capabilities=capabilities,
            generation=generation,
            collected_monotonic=collected_monotonic,
            device_info_collected_monotonic=device_info_collected_monotonic,
            captured_at=captured_at(),
            monotonic_now=monotonic_now(),
        )

    def wait_for_change(
        self,
        *,
        connection_generation: int,
        snapshot_sequence: int,
        timeout_s: float,
        wake_on_command_feedback: bool = True,
    ) -> HandSnapshot | None:
        """Return an exact visible cache change, or ``None`` after a timeout."""

        _validate_wait_arguments(
            connection_generation=connection_generation,
            snapshot_sequence=snapshot_sequence,
            timeout_s=timeout_s,
            wake_on_command_feedback=wake_on_command_feedback,
        )

        condition = (
            self._changed
            if wake_on_command_feedback
            else self._changed_without_command_feedback
        )
        with condition:
            changed = condition.wait_for(
                lambda: self._has_visible_update_locked(
                    connection_generation=connection_generation,
                    snapshot_sequence=snapshot_sequence,
                    wake_on_command_feedback=wake_on_command_feedback,
                ),
                timeout=float(timeout_s),
            )
            if not changed:
                return None
            state = self._current
            if wake_on_command_feedback:
                snapshot = state.last_change
            else:
                snapshot = state.last_non_command_feedback_change
            capabilities = state.capabilities
        return replace(
            snapshot,
            joints=self._joint_projection.get(snapshot, capabilities),
        )

    def view(self, *, captured_at: float, monotonic_now: float) -> HandSnapshot:
        """Return an immutable cache-only view with current age and stale flags."""

        _validate_time(captured_at)
        _validate_time(monotonic_now)
        with self._lock:
            state = self._current
            snapshot = state.snapshot
            capabilities = state.capabilities
            generation = state.generation
            collected_monotonic = dict(state.collected_monotonic)
            device_info_collected_monotonic = state.device_info_collected_monotonic

        return _build_view(
            snapshot,
            joint_projection=self._joint_projection,
            capabilities=capabilities,
            generation=generation,
            collected_monotonic=collected_monotonic,
            device_info_collected_monotonic=device_info_collected_monotonic,
            captured_at=captured_at,
            monotonic_now=monotonic_now,
        )

    def sample(
        self,
        semantic: TelemetrySemantic,
        *,
        captured_at: float,
        monotonic_now: float,
    ) -> object | None:
        """Return one aged cache sample without protocol access."""

        if not isinstance(semantic, TelemetrySemantic):
            raise ValidationError("semantic must be TelemetrySemantic")
        _validate_time(captured_at)
        _validate_time(monotonic_now)
        with self._lock:
            state = self._current
            sample = getattr(state.snapshot, SAMPLE_ATTRIBUTES[semantic])
            capabilities = state.capabilities
            generation = state.generation
            collected_monotonic = state.collected_monotonic.get(semantic)
            force_collected = (
                dict(state.collected_monotonic) if semantic in FORCE_QUERIES else None
            )
        if sample is None:
            return None
        return age_sample(
            semantic,
            sample,
            monotonic_now=monotonic_now,
            collected_monotonic=collected_monotonic,
            capabilities=capabilities,
            generation=generation,
            force_collected=force_collected,
        )

    def sequence(self, semantic: TelemetrySemantic) -> int:
        """Return the current generation's channel sequence."""

        with self._lock:
            return self._current.sequences.get(semantic, 0)

    @property
    def device_info_sequence(self) -> int:
        """Return the current generation's identity sample sequence."""

        with self._lock:
            return self._current.device_info_sequence

    def data_ages(
        self, *, captured_at: float, monotonic_now: float
    ) -> dict[TelemetrySemantic, DataAge]:
        """Return health metadata derived only from canonical cached samples."""

        _validate_time(captured_at)
        _validate_time(monotonic_now)
        with self._lock:
            state = self._current
            snapshot = state.snapshot
            capabilities = state.capabilities
            generation = state.generation
            collected_monotonic = dict(state.collected_monotonic)
        result: dict[TelemetrySemantic, DataAge] = {}
        for semantic, attribute in SAMPLE_ATTRIBUTES.items():
            if capabilities.telemetry_channel(semantic) is None:
                continue
            sample = getattr(snapshot, attribute)
            if sample is None:
                continue
            meta = sample.meta
            if (
                isinstance(sample, ForceSample)
                and sample.query_channel != semantic.value
            ):
                meta = next(
                    (
                        field.meta
                        for field in sample.fields
                        if field.query_channel == semantic.value
                    ),
                    SampleMeta.unavailable(connection_generation=generation),
                )
            result[semantic] = _data_age(
                semantic,
                meta,
                monotonic_now=monotonic_now,
                collected_monotonic=collected_monotonic.get(semantic),
                capabilities=capabilities,
                generation=generation,
            )
        return result

    def _state_for_generation_locked(self, generation: int) -> _GenerationState:
        if type(generation) is not int:
            raise ProtocolError("sample belongs to a stale connection generation")
        candidate = self._candidate
        if candidate is not None and candidate.generation == generation:
            return candidate
        if self._current.generation == generation:
            return self._current
        raise ProtocolError("sample belongs to a stale connection generation")

    def _has_visible_update_locked(
        self,
        *,
        connection_generation: int,
        snapshot_sequence: int,
        wake_on_command_feedback: bool,
    ) -> bool:
        state = self._current
        if state.generation != connection_generation:
            return True
        if wake_on_command_feedback:
            return state.snapshot.snapshot_sequence != snapshot_sequence
        return (
            state.last_non_command_feedback_change.snapshot_sequence > snapshot_sequence
        )


def _generation_state(
    capabilities: DeviceCapabilities,
    snapshot: HandSnapshot,
) -> _GenerationState:
    return _GenerationState(
        capabilities=capabilities,
        generation=snapshot.connection_generation,
        snapshot=snapshot,
        collected_monotonic={},
        device_info_collected_monotonic=None,
        sequences={semantic: 0 for semantic in SAMPLE_ATTRIBUTES},
        device_info_sequence=0,
        last_change=snapshot,
        last_change_collected_monotonic={},
        last_change_device_info_collected_monotonic=None,
        last_non_command_feedback_change=snapshot,
        last_non_command_feedback_collected_monotonic={},
        last_non_command_feedback_device_info_collected_monotonic=None,
    )


def _record_visible_change(
    state: _GenerationState,
    *,
    command_feedback: bool,
) -> None:
    collected_monotonic = dict(state.collected_monotonic)
    state.last_change = state.snapshot
    state.last_change_collected_monotonic = collected_monotonic
    state.last_change_device_info_collected_monotonic = (
        state.device_info_collected_monotonic
    )
    if command_feedback:
        return
    state.last_non_command_feedback_change = state.snapshot
    state.last_non_command_feedback_collected_monotonic = collected_monotonic
    state.last_non_command_feedback_device_info_collected_monotonic = (
        state.device_info_collected_monotonic
    )


def _build_view(
    snapshot: HandSnapshot,
    *,
    joint_projection: JointProjection,
    capabilities: DeviceCapabilities,
    generation: int,
    collected_monotonic: dict[TelemetrySemantic, float],
    device_info_collected_monotonic: float | None,
    captured_at: float,
    monotonic_now: float,
) -> HandSnapshot:
    _validate_time(captured_at)
    _validate_time(monotonic_now)
    updates: dict[str, object] = {"captured_at": captured_at}
    for semantic, attribute in SAMPLE_ATTRIBUTES.items():
        if attribute in updates:
            continue
        sample = getattr(snapshot, attribute)
        if sample is not None:
            updates[attribute] = age_sample(
                semantic,
                sample,
                monotonic_now=monotonic_now,
                collected_monotonic=collected_monotonic.get(semantic),
                capabilities=capabilities,
                generation=generation,
                force_collected=collected_monotonic
                if semantic in FORCE_QUERIES
                else None,
            )
        else:
            updates[attribute] = None
    device_info = snapshot.device_info
    if device_info is not None:
        updates["device_info"] = age_device_info(
            device_info,
            monotonic_now=monotonic_now,
            collected_monotonic=device_info_collected_monotonic,
            generation=generation,
        )
    updates["joints"] = joint_projection.get(snapshot, capabilities, samples=updates)
    return replace(snapshot, **updates)


def _validate_wait_arguments(
    *,
    connection_generation: int,
    snapshot_sequence: int,
    timeout_s: float,
    wake_on_command_feedback: bool,
) -> None:
    if type(connection_generation) is not int or connection_generation < 0:
        raise ValidationError("connection_generation must be a non-negative int")
    if type(snapshot_sequence) is not int or snapshot_sequence < 0:
        raise ValidationError("snapshot_sequence must be a non-negative int")
    if not isinstance(wake_on_command_feedback, bool):
        raise ValidationError("wake_on_command_feedback must be bool")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValidationError("timeout_s must be finite and positive")


def _data_age(
    semantic: TelemetrySemantic,
    metadata: SampleMeta,
    *,
    monotonic_now: float,
    collected_monotonic: float | None,
    capabilities: DeviceCapabilities,
    generation: int,
) -> DataAge:
    """Build health metadata without constructing an aged public sample."""

    if not metadata.valid or collected_monotonic is None:
        valid = metadata.valid and collected_monotonic is not None
        return DataAge(
            channel=semantic.value,
            age_s=None,
            valid=valid,
            stale=True,
            sequence=metadata.sequence if valid else 0,
            connection_generation=generation,
            origin=metadata.origin if valid else DataOrigin.NONE,
        )
    age_s = max(0.0, monotonic_now - collected_monotonic)
    telemetry = capabilities.telemetry_channel(semantic)
    return DataAge(
        channel=semantic.value,
        age_s=age_s,
        valid=True,
        stale=telemetry is None or age_s > telemetry.stale_after_s,
        sequence=metadata.sequence,
        connection_generation=generation,
        origin=metadata.origin,
    )


def _validate_time(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValidationError("snapshot time must be finite numeric")


__all__ = ["SnapshotStore"]
