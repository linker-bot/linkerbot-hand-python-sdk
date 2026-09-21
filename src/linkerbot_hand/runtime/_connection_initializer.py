"""Identity verification and baseline initialization on the actor thread."""

from __future__ import annotations

from ..api.config import PollingPlan
from ..api.device_info import DeviceInfo
from ..api.errors import ProtocolError
from ..capabilities.types import TelemetrySemantic
from ..protocols.base import IdentityResult, ProtocolBackend
from ._clock import RuntimeClock
from ._runtime_state import RuntimeState
from ._transaction_runner import TransactionRunner
from .command_scheduler import CommandIngress
from .metrics import RuntimeMetrics
from .poll_scheduler import PollScheduler
from .snapshot_store import SnapshotStore
from .transactions import TransactionCoordinator


class ConnectionInitializer:
    """Build one verified connection generation without owning a thread."""

    def __init__(
        self,
        *,
        backend: ProtocolBackend,
        state: RuntimeState,
        commands: CommandIngress,
        transactions: TransactionCoordinator,
        polling: PollScheduler,
        snapshot_store: SnapshotStore,
        metrics: RuntimeMetrics,
        clock: RuntimeClock,
        identity_timeout_s: float,
    ) -> None:
        self._backend = backend
        self._state = state
        self._commands = commands
        self._transactions = transactions
        self._polling = polling
        self._snapshot_store = snapshot_store
        self._metrics = metrics
        self._clock = clock
        self._identity_timeout_s = identity_timeout_s

    def initialize(
        self,
        runner: TransactionRunner,
        *,
        recovery: bool,
        polling_plan: PollingPlan | None,
    ) -> None:
        """Verify identity, read baseline, then atomically open admission."""

        # Every connection generation starts from the declared latest-protocol
        # profile. Runtime feature probes from a prior connection must not leak
        # into a newly connected device or firmware image.
        current = self._state.declared
        identity_outcome = runner.execute(
            self._backend.identity_transaction(current),
            capabilities=current,
            publish_generation=None,
            latest_family=None,
            priority=100,
            response_timeout_s=self._identity_timeout_s,
        )
        identity = identity_outcome.result
        if not isinstance(identity, IdentityResult):
            raise ProtocolError("identity transaction did not return IdentityResult")
        self._state.validate_identity(identity, recovery=recovery)
        runner.raise_if_interrupted()
        generation = self._state.generation() + 1
        now = self._clock.monotonic()
        self._snapshot_store.begin_generation(
            identity.capabilities,
            generation=generation,
            captured_at=self._clock.time(),
        )
        self._snapshot_store.publish_device_info(
            identity.device_info,
            generation=generation,
            collected_at=self._clock.time(),
            collected_monotonic=now,
        )
        for semantic in self._baseline_semantics(identity):
            outcome = runner.execute(
                self._backend.read_transaction(semantic, identity.capabilities),
                capabilities=identity.capabilities,
                publish_generation=generation,
                latest_family=None,
                priority=90,
            )
            if not any(
                payload.semantic is semantic for payload, _ in outcome.publications
            ):
                raise ProtocolError(
                    f"baseline transaction did not publish {semantic.value}"
                )
        runner.raise_if_interrupted()
        self._polling.reconfigure(identity.capabilities)
        if recovery and polling_plan is not None:
            self._polling.replace(polling_plan, now=self._clock.monotonic())
        self._commands.activate(generation=generation)
        self._transactions.activate()
        self._snapshot_store.commit_generation(generation=generation)
        self._state.commit_identity(identity, generation=generation)
        self._metrics.ready(generation=generation, recovered=recovery)

    def refresh_identity(
        self,
        runner: TransactionRunner,
    ) -> tuple[DeviceInfo, int]:
        """Actively verify and publish identity in the current generation."""

        capabilities = self._state.capabilities()
        outcome = runner.execute(
            self._backend.identity_transaction(capabilities),
            capabilities=capabilities,
            publish_generation=None,
            latest_family=None,
            priority=80,
            response_timeout_s=self._identity_timeout_s,
        )
        identity = outcome.result
        if not isinstance(identity, IdentityResult):
            raise ProtocolError("identity refresh returned invalid data")
        self._state.validate_identity(identity, recovery=True)
        published = self._snapshot_store.publish_device_info(
            identity.device_info,
            generation=self._state.generation(),
            collected_at=self._clock.time(),
            collected_monotonic=self._clock.monotonic(),
        )
        return published, self._snapshot_store.device_info_sequence

    def _baseline_semantics(
        self,
        identity: IdentityResult,
    ) -> tuple[TelemetrySemantic, ...]:
        baseline = self._backend.baseline_semantics(identity.capabilities)
        if not isinstance(baseline, tuple) or any(
            not isinstance(value, TelemetrySemantic) for value in baseline
        ):
            raise ProtocolError("baseline_semantics returned invalid values")
        if len(set(baseline)) != len(baseline):
            raise ProtocolError("baseline semantics must be unique")
        if TelemetrySemantic.ANGLES not in baseline:
            raise ProtocolError("baseline must include angles")
        for semantic in baseline:
            telemetry = identity.capabilities.telemetry_channel(semantic)
            if telemetry is None or not telemetry.device_queried:
                raise ProtocolError(f"baseline cannot actively read {semantic.value}")
        return baseline


__all__ = ["ConnectionInitializer"]
