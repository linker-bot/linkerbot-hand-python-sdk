"""Copy-on-write canonical state store for Hand SDK 1.0."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace

from ..api._initial_state import make_initial_snapshot
from ..api.device_info import DeviceInfo
from ..api.enums import DataOrigin
from ..api.errors import ProtocolError, ValidationError
from ..api.health import DataAge
from ..api.readings import AngleSample, ForceSample, SampleMeta, _RawVectorSample
from ..api.snapshot import HandSnapshot
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..protocols.base import SamplePayload
from ._snapshot_samples import (
    SAMPLE_ATTRIBUTES,
    age_device_info,
    age_sample,
    build_joints,
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
        self._current = _generation_state(capabilities, initial_snapshot)
        self._candidate: _GenerationState | None = None

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
            updated = replace(
                state.snapshot,
                captured_at=collected_at,
                snapshot_sequence=state.snapshot.snapshot_sequence + 1,
                **{attribute: sample},
            )
            state.snapshot = replace(
                updated,
                joints=build_joints(updated, state.capabilities),
            )
            state.sequences[payload.semantic] = sequence
            state.collected_monotonic[payload.semantic] = collected_monotonic
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
            self._candidate = None

    def view(self, *, captured_at: float, monotonic_now: float) -> HandSnapshot:
        """Return an immutable cache-only view with current age and stale flags."""

        _validate_time(captured_at)
        _validate_time(monotonic_now)
        with self._lock:
            state = self._current
            updates: dict[str, object] = {"captured_at": captured_at}
            for semantic, attribute in SAMPLE_ATTRIBUTES.items():
                sample = getattr(state.snapshot, attribute)
                if sample is not None:
                    updates[attribute] = age_sample(
                        semantic,
                        sample,
                        monotonic_now=monotonic_now,
                        collected_monotonic=state.collected_monotonic.get(semantic),
                        capabilities=state.capabilities,
                        generation=state.generation,
                    )
            device_info = state.snapshot.device_info
            if device_info is not None:
                updates["device_info"] = age_device_info(
                    device_info,
                    monotonic_now=monotonic_now,
                    collected_monotonic=state.device_info_collected_monotonic,
                    generation=state.generation,
                )
            return replace(state.snapshot, **updates)

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
        return getattr(
            self.view(captured_at=captured_at, monotonic_now=monotonic_now),
            SAMPLE_ATTRIBUTES[semantic],
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

        snapshot = self.view(captured_at=captured_at, monotonic_now=monotonic_now)
        result: dict[TelemetrySemantic, DataAge] = {}
        for semantic, attribute in SAMPLE_ATTRIBUTES.items():
            sample = getattr(snapshot, attribute)
            if sample is None:
                continue
            result[semantic] = DataAge(
                channel=semantic.value,
                age_s=sample.meta.age_s,
                valid=sample.meta.valid,
                stale=sample.meta.stale,
                sequence=sample.meta.sequence,
                connection_generation=sample.meta.connection_generation,
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
    )


def _validate_time(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValidationError("snapshot time must be finite numeric")


__all__ = ["SnapshotStore"]
