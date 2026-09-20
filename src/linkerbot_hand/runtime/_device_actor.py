"""Single device actor owning protocol work, polling, and safe recovery."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from ..api.commands import AckSupport, CommandFamily, CommandStage
from ..api.config import DeviceConfig, PollingPlan
from ..api.enums import DataOrigin
from ..api.errors import (
    DeviceNotReadyError,
    HandError,
    IdentityMismatchError,
    ProtocolError,
    TimeoutError,
    TransportError,
    UnsupportedFirmwareError,
)
from ..api.readings import ForceSample
from ..capabilities.force import FORCE_QUERIES
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..protocols.base import ProtocolBackend, ProtocolTransaction
from ._clock import RuntimeClock
from ._connection_initializer import ConnectionInitializer
from ._errors import as_hand_error
from ._operation_ticket import OperationTicket
from ._route_mailbox import RouteMailbox
from ._runtime_state import RuntimeState
from ._transaction_runner import (
    PendingTransaction,
    TransactionRunner,
    TransactionSuperseded,
)
from ._transport_arbiter import BatchDispatchError
from ._transport_binding import TransportBinding
from .command_scheduler import CommandIngress, ScheduledCommand, SchedulingPolicy
from .metrics import RuntimeMetrics
from .poll_scheduler import PollScheduler
from .snapshot_store import SnapshotStore
from .transactions import DEVICE_INFO_KEY, TransactionClaim, TransactionCoordinator

_LOGGER = logging.getLogger(__name__)
_FIRMWARE_OPTIONAL_FAILURE_THRESHOLD = 3
_MAX_INTERSTAGE_ANGLE_TRANSACTIONS = 4


@dataclass(slots=True)
class _RecoveryState:
    ticket: OperationTicket | None = None
    due_at_s: float | None = None
    delay_s: float = 0.0


class DeviceActor:
    """Execute all stateful device work on one owner thread."""

    def __init__(
        self,
        *,
        config: DeviceConfig,
        backend: ProtocolBackend,
        binding: TransportBinding,
        state: RuntimeState,
        commands: CommandIngress,
        transactions: TransactionCoordinator,
        polling: PollScheduler,
        snapshot_store: SnapshotStore,
        metrics: RuntimeMetrics,
        clock: RuntimeClock,
        wake: threading.Event,
    ) -> None:
        self._config = config
        self._backend = backend
        self._binding = binding
        self._state = state
        self._commands = commands
        self._transactions = transactions
        self._polling = polling
        self._snapshot_store = snapshot_store
        self._metrics = metrics
        self._clock = clock
        self._wake = wake
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mailbox = RouteMailbox(
            binding=binding,
            route_keys=backend.route_keys,
            route_capacity=config.route_queue_capacity,
            mailbox_capacity=config.mailbox_capacity,
            wake=wake,
        )
        self._initializer = ConnectionInitializer(
            backend=backend,
            state=state,
            commands=commands,
            transactions=transactions,
            polling=polling,
            snapshot_store=snapshot_store,
            metrics=metrics,
            clock=clock,
        )
        self._control_lock = threading.Lock()
        self._initial_ticket: OperationTicket | None = None
        self._recovery = _RecoveryState(delay_s=config.reconnect_initial_delay_s)
        self._performing_recovery = False
        self._saved_polling_plan: PollingPlan | None = None
        self._next_command_at = 0.0
        self._prefer_transaction = False
        self._prefer_inbound = False
        self._firmware_optional_failures: dict[TelemetrySemantic, int] = {}
        self._verified_firmware_optional: set[TelemetrySemantic] = set()

    def start(self) -> OperationTicket:
        """Start one actor generation; initialization remains actor-owned."""

        with self._control_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._initial_ticket is None:
                    raise ProtocolError("actor has no initialization ticket")
                return self._initial_ticket
            self._stop = threading.Event()
            self._mailbox.reset()
            self._initial_ticket = OperationTicket()
            self._recovery = _RecoveryState(
                delay_s=self._config.reconnect_initial_delay_s
            )
            self._performing_recovery = False
            self._saved_polling_plan = None
            self._next_command_at = 0.0
            self._prefer_transaction = False
            self._prefer_inbound = False
            self._firmware_optional_failures.clear()
            self._verified_firmware_optional.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"LinkerbotHand.{self._state.declared.model}.actor",
                daemon=False,
            )
            self._thread.start()
            return self._initial_ticket

    def request_recovery(self) -> OperationTicket:
        """Coalesce explicit recovery callers onto the next safe generation."""

        not_ready = DeviceNotReadyError(
            "device recovery was requested",
            operation="recover",
            model=self._state.declared.model,
            generation=self._state.generation(),
        )
        self._state.stop_admission()
        self._commands.reject_new(not_ready)
        self._transactions.reject_new(not_ready)
        with self._control_lock:
            ticket = self._recovery.ticket
            if ticket is None or ticket.event.is_set():
                ticket = OperationTicket()
                self._recovery.ticket = ticket
                prepare_transport = not self._performing_recovery
                if prepare_transport:
                    self._performing_recovery = True
            else:
                prepare_transport = False
        if prepare_transport:
            drain_failure: HandError | None = None
            try:
                # Make queued transport work unreachable before its public handle
                # can become FAILED. A bounded drain failure is returned to the
                # caller instead of scheduling a delayed, unobserved recovery.
                self._binding.stop_admission_and_drain()
            except Exception as error:  # noqa: BLE001 - recovery preparation boundary
                drain_failure = self._classify(error, "recover")
                self._metrics.record_error(drain_failure)
        else:
            drain_failure = None
        self._commands.fail_pending(not_ready)
        self._transactions.fail_all(not_ready)
        with self._control_lock:
            if prepare_transport:
                self._performing_recovery = False
            if drain_failure is None:
                self._recovery.due_at_s = self._clock.monotonic()
            else:
                ticket.fail(drain_failure)
                self._recovery.due_at_s = None
        self._wake.set()
        return ticket

    def stop(self, *, timeout_s: float) -> None:
        """Stop route admission and join actor/receiver within one deadline."""

        deadline = self._clock.monotonic() + timeout_s
        self._stop.set()
        self._wake.set()
        self._mailbox.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline - self._clock.monotonic()))
            if thread.is_alive():
                raise TimeoutError("device actor did not stop before shutdown deadline")
        self._mailbox.join(deadline_s=deadline)
        with self._control_lock:
            ticket = self._recovery.ticket
            if ticket is not None:
                ticket.fail(
                    DeviceNotReadyError(
                        "device runtime stopped during recovery",
                        operation="recover",
                        model=self._state.declared.model,
                        generation=self._state.generation(),
                    )
                )
            self._recovery = _RecoveryState(
                delay_s=self._config.reconnect_initial_delay_s
            )
            self._saved_polling_plan = None
            self._thread = None

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        initial = self._initial_ticket
        if initial is None:
            return
        try:
            self._mailbox.start()
            runner = self._make_runner()
            self._initializer.initialize(
                runner,
                recovery=False,
                polling_plan=None,
            )
        except Exception as error:  # noqa: BLE001 - actor lifecycle boundary
            classified = self._classify(error, "open")
            self._reject_failed_initialization(classified)
            self._metrics.faulted(classified)
            initial.fail(classified)
            self._stop.set()
        else:
            initial.succeed()
            self._run_main_loop(runner)
        finally:
            self._mailbox.stop(
                deadline_s=self._clock.monotonic() + self._config.shutdown_timeout_s
            )

    def _run_main_loop(self, runner: TransactionRunner) -> None:
        """Run the loop behind a fault boundary so the thread never dies silently.

        ``open()`` has already succeeded here, so an escaping exception would
        otherwise end the actor thread while health still reported a connected
        device and every waiter blocked until its own deadline.
        """

        try:
            self._main_loop(runner)
        except BaseException as error:
            _LOGGER.exception("actor loop stopped on an unhandled error")
            self._fault_after_start(error)
            raise

    def _fault_after_start(self, error: BaseException) -> None:
        """Publish a truthful fault and release waiters after a loop crash."""

        try:
            classified = self._classify(error, "run")
            self._reject_failed_initialization(classified)
            self._metrics.faulted(classified)
        except Exception:
            _LOGGER.exception("actor fault reporting failed")
        finally:
            self._stop.set()

    def _main_loop(self, runner: TransactionRunner) -> None:
        while not self._stop.is_set():
            if self._recovery_requested():
                runner = self._perform_recovery(runner)
                continue
            receiver_error = self._mailbox.error()
            if receiver_error is not None and self._state.ready():
                self._disconnect(self._classify(receiver_error, "receive"))
                continue
            if self._auto_recovery_due():
                runner = self._perform_recovery(runner)
                continue
            if not self._state.ready():
                self._wait_for_work()
                continue

            now = self._clock.monotonic()
            angle_command_ready = (
                now >= self._next_command_at
                and self._commands.has_pending(CommandFamily.ANGLE_TARGET)
                and self._polling.angle_command_can_fulfill_feedback
            )
            canceled_angle_poll = angle_command_ready and (
                self._transactions.cancel_pending_poll(TelemetrySemantic.ANGLES)
            )
            angle_command_fulfills_due_sample = angle_command_ready and (
                canceled_angle_poll
                or self._polling.is_due(TelemetrySemantic.ANGLES, now=now)
            )
            if angle_command_fulfills_due_sample:
                command = self._commands.take_latest(CommandFamily.ANGLE_TARGET)
                if command is not None:
                    self._prefer_transaction = True
                    self._prefer_inbound = True
                    self._next_command_at = now + 1.0 / self._config.command_rate_hz
                    self._execute_command(runner, command)
                    continue
            due = self._polling.take_due(now=now)
            if due is not None:
                self._transactions.request_poll(due)
            command_ready = (
                self._commands.pending_count > 0 and now >= self._next_command_at
            )
            transaction_ready = self._transactions.pending_count > 0
            if self._prefer_inbound and self._process_inbound(runner):
                self._prefer_inbound = False
                continue
            if transaction_ready and (not command_ready or self._prefer_transaction):
                claim = self._transactions.take_next()
                if claim is not None:
                    self._prefer_transaction = False
                    self._prefer_inbound = True
                    self._execute_read(runner, claim)
                    continue
            if command_ready:
                command = self._commands.take_next()
                if command is not None:
                    self._prefer_transaction = True
                    self._prefer_inbound = True
                    self._next_command_at = now + 1.0 / self._config.command_rate_hz
                    self._execute_command(runner, command)
                    continue
            if self._process_inbound(runner):
                self._prefer_inbound = False
                continue
            self._wait_for_work()

    def _process_inbound(self, runner: TransactionRunner) -> bool:
        """Process one report so sustained scheduled work cannot starve RX."""

        inbound = self._mailbox.pop()
        if inbound is None:
            return False
        try:
            runner.process_unsolicited(
                inbound,
                capabilities=self._state.capabilities(),
                generation=self._state.generation(),
            )
        except Exception as error:  # noqa: BLE001 - report boundary
            self._metrics.record_error(self._classify(error, "report"))
        return True

    def _execute_command(
        self, runner: TransactionRunner, command: ScheduledCommand
    ) -> None:
        active = command
        try:
            capabilities = self._state.capabilities()
            transaction = self._build_command_transaction(active, capabilities)
            pending = runner.begin(
                transaction,
                capabilities=capabilities,
                publish_generation=self._state.generation(),
                latest_family=(
                    active.family if active.policy is SchedulingPolicy.LATEST else None
                ),
                priority=100,
            )
            if active.policy is SchedulingPolicy.LATEST:
                active, pending = self._replace_unclaimed_target(
                    runner,
                    active,
                    pending,
                    capabilities=capabilities,
                )
            handle = active.handle
            expects_ack = handle.ack_support is AckSupport.SUPPORTED

            def mark_dispatched(timestamp: float, sent_frames: int) -> None:
                handle._mark_dispatched(
                    timestamp=timestamp,
                    sent_frames=sent_frames,
                )
                self._commands.record_stage(handle, CommandStage.DISPATCHED)

            outcome = runner.complete(
                pending,
                on_dispatched=mark_dispatched,
            )
            self._record_command_feedback(outcome.publications)
            if expects_ack:
                if not outcome.acknowledged:
                    raise ProtocolError("device response did not acknowledge command")
                handle._mark_acknowledged(timestamp=self._clock.monotonic())
                self._commands.record_stage(handle, CommandStage.ACKNOWLEDGED)
        except TransactionSuperseded:
            active.handle._mark_superseded(timestamp=self._clock.monotonic())
            self._commands.record_stage(active.handle, CommandStage.SUPERSEDED)
        except Exception as error:  # noqa: BLE001 - command handle owns failure
            classified = self._classify(error, "command")
            if active.family is CommandFamily.ANGLE_TARGET and isinstance(
                classified, TimeoutError
            ):
                self._polling.mark_command_feedback_timeout()
            handle = active.handle
            if not handle.done:
                sent_frames = _sent_frames(error, handle.sent_frames)
                handle._mark_failed(
                    classified,
                    timestamp=self._clock.monotonic(),
                    sent_frames=sent_frames,
                )
                self._commands.record_stage(handle, CommandStage.FAILED)
            if _connection_failure(classified):
                # Nested interstage work may already have ended this generation
                # and disconnected; a second _disconnect would clobber the saved
                # polling plan, so only disconnect the generation still running.
                if self._state.ready():
                    self._disconnect(classified)
            else:
                self._metrics.record_error(classified)

    def _record_command_feedback(
        self,
        publications: tuple[tuple[object, object], ...],
    ) -> None:
        """Let verified angle-command feedback fulfill the periodic plan."""

        has_angle_feedback = any(
            getattr(payload, "semantic", None) is TelemetrySemantic.ANGLES
            and getattr(payload, "origin", None) is DataOrigin.DEVICE_COMMAND_FEEDBACK
            for payload, _sample in publications
        )
        if not has_angle_feedback:
            return
        if self._polling.mark_angle_command_feedback(now=self._clock.monotonic()):
            self._transactions.cancel_pending_poll(TelemetrySemantic.ANGLES)

    def _replace_unclaimed_target(
        self,
        runner: TransactionRunner,
        active: ScheduledCommand,
        pending: PendingTransaction,
        *,
        capabilities: DeviceCapabilities,
    ) -> tuple[ScheduledCommand, PendingTransaction]:
        """Push newer targets through the final transport latest slot."""

        while not pending.receipt.done() and pending.receipt.claimed_at_s is None:
            self._wake.clear()
            replacement = self._commands.take_latest(active.family)
            if replacement is not None:
                try:
                    transaction = self._build_command_transaction(
                        replacement,
                        capabilities,
                    )
                except Exception as error:  # noqa: BLE001 - handle owns failure
                    classified = self._classify(error, "command")
                    replacement.handle._mark_failed(
                        classified,
                        timestamp=self._clock.monotonic(),
                        sent_frames=0,
                    )
                    self._commands.record_stage(
                        replacement.handle,
                        CommandStage.FAILED,
                    )
                    self._metrics.record_error(classified)
                    continue
                try:
                    updated = runner.replace_pending(
                        transaction,
                        capabilities=capabilities,
                        publish_generation=self._state.generation(),
                        latest_family=active.family,
                        priority=100,
                    )
                except Exception:
                    self._commands.restore_latest(replacement)
                    raise
                if updated is None:
                    self._commands.restore_latest(replacement)
                    break
                active.handle._mark_superseded(timestamp=self._clock.monotonic())
                self._commands.record_stage(active.handle, CommandStage.SUPERSEDED)
                active = replacement
                pending = updated
                self._process_inbound(runner)
                continue
            if pending.receipt.done() or pending.receipt.claimed_at_s is not None:
                break
            if self._stop.is_set() or self._mailbox.error() is not None:
                break
            if self._process_inbound(runner):
                continue
            runner.raise_if_interrupted()
            self._clock.wait(self._wake, None)
        return active, pending

    def _build_command_transaction(
        self,
        command: ScheduledCommand,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        transaction = (
            self._backend.command_transaction(
                command.semantic,
                command.values,
                capabilities,
            )
            if command.semantic is not None
            else self._backend.operation_transaction(
                command.operation or "",
                capabilities,
            )
        )
        expects_ack = command.handle.ack_support is AckSupport.SUPPORTED
        if transaction.terminal_acknowledges_command != expects_ack:
            raise ProtocolError("transaction ACK behavior disagrees with capability")
        return transaction

    def _execute_read(self, runner: TransactionRunner, claim: TransactionClaim) -> None:
        poll_in_flight = False
        transaction: ProtocolTransaction | None = None
        try:
            if claim.key == DEVICE_INFO_KEY:
                published, sequence = self._initializer.refresh_identity(runner)
                self._transactions.complete(
                    claim.key,
                    published,
                    sequence=sequence,
                )
                return
            if not isinstance(claim.key, TelemetrySemantic):
                raise ProtocolError("active transaction key is not telemetry")
            semantic = claim.key
            if claim.requested_by_poll:
                poll_in_flight = self._polling.begin_poll(semantic)
                if not poll_in_flight and self._transactions.cancel_poll_if_unobserved(
                    semantic
                ):
                    return
            capabilities = self._state.capabilities()
            transaction = self._backend.read_transaction(semantic, capabilities)
            outcome = runner.execute(
                transaction,
                capabilities=capabilities,
                publish_generation=self._state.generation(),
                latest_family=None,
                priority=80,
                on_stage_boundary=lambda next_stage_duration_s: (
                    self._dispatch_interstage_angle_work(
                        runner,
                        next_stage_duration_s=next_stage_duration_s,
                    )
                ),
                require_published_semantic=semantic,
            )
            published = next(
                (
                    sample
                    for payload, sample in outcome.publications
                    if payload.semantic is semantic
                ),
                None,
            )
            if published is None:
                # Unreachable: require_published_semantic makes the runner fail
                # the transaction before it clears the consecutive-error count.
                raise ProtocolError(
                    f"read transaction did not publish {semantic.value}"
                )
            sequence = self._snapshot_store.sequence(semantic)
            if isinstance(published, ForceSample):
                observed = self._state.observe_force_sample(published)
                self._snapshot_store.observe_force_capabilities(
                    observed, generation=published.meta.connection_generation
                )
                self._polling.observe_force_capabilities(observed)
            self._transactions.complete(semantic, published, sequence=sequence)
            if transaction.firmware_optional:
                self._firmware_optional_failures.pop(semantic, None)
                self._verified_firmware_optional.add(semantic)
            self._polling.mark_completed(
                semantic,
                now=self._clock.monotonic(),
                requested_by_poll=poll_in_flight,
            )
        except Exception as error:  # noqa: BLE001 - transaction waiter boundary
            classified = self._classify(error, "read")
            if (
                isinstance(claim.key, TelemetrySemantic)
                and transaction is not None
                and transaction.firmware_optional
                and _sent_frames(error, 0) > 0
                and claim.key not in self._verified_firmware_optional
            ):
                # Never let bookkeeping failures skip the waiter release below.
                try:
                    classified = self._firmware_optional_failure(
                        claim.key,
                        error,
                        classified,
                    )
                except Exception:
                    _LOGGER.exception(
                        "optional-query degradation failed for %s",
                        claim.key.value,
                    )
            self._transactions.fail(claim.key, classified)
            if poll_in_flight and isinstance(claim.key, TelemetrySemantic):
                self._polling.mark_timeout(claim.key)
            identity_failure = claim.key == DEVICE_INFO_KEY and isinstance(
                classified,
                IdentityMismatchError,
            )
            if _connection_failure(classified) or identity_failure:
                # See _execute_command: interstage work may already have
                # disconnected this generation, so avoid a second _disconnect.
                if self._state.ready():
                    self._disconnect(classified)
            else:
                self._metrics.record_error(classified)
        finally:
            if poll_in_flight:
                self._polling.finish_poll()

    def _dispatch_interstage_angle_work(
        self,
        runner: TransactionRunner,
        *,
        next_stage_duration_s: float,
    ) -> None:
        """Dispatch bounded due angle work at one protocol-safe boundary.

        One command and one active query usually share a deadline.  Two
        symmetric pairs absorb ordinary phase drift around a tactile-finger
        boundary, while the hard bound prevents angle traffic from starving
        the next finger stage. If an already queued target becomes eligible
        before that stage is expected to finish, preserve the configured
        command interval by waiting at the safe boundary instead of delaying
        it behind another multi-frame response.
        """

        dispatched = 0
        while dispatched < _MAX_INTERSTAGE_ANGLE_TRANSACTIONS:
            if self._dispatch_one_interstage_angle_transaction(runner):
                self._raise_if_generation_ended()
                dispatched += 1
                continue
            if not self._wait_for_imminent_interstage_angle_command(
                runner,
                next_stage_duration_s=next_stage_duration_s,
            ):
                return

    def _raise_if_generation_ended(self) -> None:
        """Abort the outer transaction when nested work ended this generation.

        Interstage angle work runs re-entrantly inside a multi-stage read's
        stage boundary. A dispatched command or read that hits a connection
        failure disconnects in place and returns normally, so this loop — and
        the outer transaction that would otherwise submit its next stage — must
        stop before either sends a frame to a generation that has ended.
        """

        if self._state.ready():
            return
        raise DeviceNotReadyError(
            "connection generation ended during interstage angle work",
            operation="recover",
            model=self._state.declared.model,
            generation=self._state.generation(),
        )

    def _wait_for_imminent_interstage_angle_command(
        self,
        runner: TransactionRunner,
        *,
        next_stage_duration_s: float,
    ) -> bool:
        """Wait for one queued target due before the next protocol boundary."""

        now = self._clock.monotonic()
        horizon = now + next_stage_duration_s
        while self._commands.has_pending(CommandFamily.ANGLE_TARGET):
            runner.raise_if_interrupted()
            now = self._clock.monotonic()
            if now >= self._next_command_at:
                return True
            if self._next_command_at > horizon:
                return False
            self._wake.clear()
            if not self._commands.has_pending(CommandFamily.ANGLE_TARGET):
                return False
            self._clock.wait(self._wake, self._next_command_at - now)
        return False

    def _dispatch_one_interstage_angle_transaction(
        self,
        runner: TransactionRunner,
    ) -> bool:
        """Dispatch one due angle transaction and report whether work ran."""

        runner.raise_if_interrupted()
        now = self._clock.monotonic()
        command_ready = now >= self._next_command_at and self._commands.has_pending(
            CommandFamily.ANGLE_TARGET
        )
        command_fulfills_due_sample = (
            command_ready
            and self._polling.angle_command_can_fulfill_feedback
            and (
                self._transactions.cancel_pending_poll(TelemetrySemantic.ANGLES)
                or self._polling.is_due(TelemetrySemantic.ANGLES, now=now)
            )
        )
        if command_fulfills_due_sample:
            command = self._commands.take_latest(CommandFamily.ANGLE_TARGET)
            if command is not None:
                self._prefer_transaction = True
                self._prefer_inbound = True
                self._next_command_at = now + 1.0 / self._config.command_rate_hz
                self._execute_command(runner, command)
                return True

        due = self._polling.take_due_channel(TelemetrySemantic.ANGLES, now=now)
        if due is not None:
            self._transactions.request_poll(due)
        transaction_ready = self._transactions.has_pending(TelemetrySemantic.ANGLES)
        if transaction_ready and (not command_ready or self._prefer_transaction):
            claim = self._transactions.take(TelemetrySemantic.ANGLES)
            if claim is not None:
                self._prefer_transaction = False
                self._prefer_inbound = True
                self._execute_read(runner, claim)
                return True
        if command_ready:
            command = self._commands.take_latest(CommandFamily.ANGLE_TARGET)
            if command is not None:
                self._prefer_transaction = True
                self._prefer_inbound = True
                self._next_command_at = now + 1.0 / self._config.command_rate_hz
                self._execute_command(runner, command)
                return True
        if transaction_ready:
            claim = self._transactions.take(TelemetrySemantic.ANGLES)
            if claim is not None:
                self._prefer_transaction = False
                self._prefer_inbound = True
                self._execute_read(runner, claim)
                return True
        return False

    def _firmware_optional_failure(
        self,
        semantic: TelemetrySemantic,
        error: BaseException,
        classified: HandError,
    ) -> HandError:
        """Count one optional-query failure and downgrade once it is conclusive."""

        if isinstance(classified, UnsupportedFirmwareError):
            return self._disable_firmware_unsupported_read(semantic, classified)
        if not isinstance(classified, (TimeoutError, ProtocolError)):
            return classified
        failures = self._firmware_optional_failures.get(semantic, 0) + 1
        self._firmware_optional_failures[semantic] = failures
        if failures >= _FIRMWARE_OPTIONAL_FAILURE_THRESHOLD:
            return self._disable_firmware_unsupported_read(semantic, error)
        _LOGGER.warning(
            "%s optional query failed %d/%d; keeping it enabled",
            semantic.value,
            failures,
            _FIRMWARE_OPTIONAL_FAILURE_THRESHOLD,
        )
        return classified

    def _disable_firmware_unsupported_read(
        self,
        semantic: TelemetrySemantic,
        cause: BaseException,
    ) -> UnsupportedFirmwareError:
        """Disable one optional query without ending the connection generation."""

        self._firmware_optional_failures.pop(semantic, None)
        try:
            capabilities = self._state.mark_firmware_unsupported_telemetry(semantic)
            if semantic in FORCE_QUERIES:
                self._snapshot_store.observe_force_capabilities(
                    capabilities, generation=self._state.generation()
                )
        except HandError as error:
            # This runs inside an ``except`` handler, so raising here would
            # escape the caller entirely. A generation that can no longer accept
            # capability updates is already ending, which makes the downgrade
            # meaningless; report the original cause instead.
            _LOGGER.warning(
                "could not disable unsupported %s: %s",
                semantic.value,
                error,
            )
            return UnsupportedFirmwareError(
                f"{self._state.declared.model} firmware does not support "
                f"{semantic.value}",
                operation=f"get_{semantic.value}",
                model=self._state.declared.model,
                generation=self._state.generation(),
                cause=cause,
            )
        self._polling.disable_firmware_unsupported(semantic, capabilities)
        error = UnsupportedFirmwareError(
            f"{capabilities.model} firmware "
            f"{capabilities.identity.firmware or 'unknown'} does not support "
            f"{semantic.value}",
            operation=f"get_{semantic.value}",
            model=capabilities.model,
            generation=self._state.generation(),
            cause=cause,
        )
        _LOGGER.warning("%s; disabling this query for the current connection", error)
        return error

    def _disconnect(self, error: HandError) -> None:
        self._state.mark_not_ready()
        self._mailbox.close()
        not_ready = DeviceNotReadyError(
            "connection generation ended",
            operation="recover",
            model=self._state.declared.model,
            generation=self._state.generation(),
            cause=error,
        )
        self._commands.reject_new(not_ready)
        self._commands.fail_pending(not_ready)
        self._transactions.reject_new(not_ready)
        self._transactions.fail_all(not_ready)
        self._saved_polling_plan = self._polling.plan
        self._polling.stop()
        generation = self._state.generation()
        if generation > 0:
            self._snapshot_store.invalidate(
                generation=generation,
                captured_at=self._clock.time(),
            )
        if self._config.auto_reconnect and not isinstance(error, IdentityMismatchError):
            self._metrics.recovering(error)
            with self._control_lock:
                self._recovery.due_at_s = (
                    self._clock.monotonic() + self._recovery.delay_s
                )
        else:
            self._metrics.faulted(error)

    def _perform_recovery(self, runner: TransactionRunner) -> TransactionRunner:
        with self._control_lock:
            self._performing_recovery = True
        self._metrics.recovering()
        if self._state.ready():
            self._disconnect(
                DeviceNotReadyError("connection replaced by explicit recovery")
            )
        try:
            self._mailbox.stop(
                deadline_s=self._clock.monotonic() + self._config.shutdown_timeout_s
            )
            self._binding.recover()
            self._mailbox.reset()
            self._mailbox.start()
            runner = self._make_runner()
            self._firmware_optional_failures.clear()
            self._verified_firmware_optional.clear()
            self._initializer.initialize(
                runner,
                recovery=True,
                polling_plan=self._saved_polling_plan,
            )
        except Exception as error:  # noqa: BLE001 - recovery lifecycle boundary
            classified = self._classify(error, "recover")
            self._reject_failed_initialization(classified)
            self._metrics.faulted(classified)
            with self._control_lock:
                ticket = self._recovery.ticket
                if ticket is not None:
                    ticket.fail(classified)
                    self._recovery.ticket = None
                if self._config.auto_reconnect and not isinstance(
                    classified, IdentityMismatchError
                ):
                    self._recovery.delay_s = min(
                        self._config.reconnect_max_delay_s,
                        max(
                            self._config.reconnect_initial_delay_s,
                            self._recovery.delay_s * 2,
                        ),
                    )
                    self._recovery.due_at_s = (
                        self._clock.monotonic() + self._recovery.delay_s
                    )
                else:
                    self._recovery.due_at_s = None
                self._performing_recovery = False
        else:
            with self._control_lock:
                self._recovery.delay_s = self._config.reconnect_initial_delay_s
                self._recovery.due_at_s = None
                ticket = self._recovery.ticket
                if ticket is not None:
                    ticket.succeed()
                    self._recovery.ticket = None
                self._performing_recovery = False
        return runner

    def _reject_failed_initialization(self, error: HandError) -> None:
        """Close every admission gate and discard a partial baseline."""

        self._state.mark_not_ready()
        not_ready = DeviceNotReadyError(
            "connection generation did not initialize",
            operation=error.operation,
            model=self._state.declared.model,
            generation=self._state.generation(),
            cause=error,
        )
        self._commands.reject_new(not_ready)
        self._commands.fail_pending(not_ready)
        self._transactions.reject_new(not_ready)
        self._transactions.fail_all(not_ready)
        self._polling.stop()
        capabilities = self._state.capabilities()
        self._polling.reconfigure(capabilities)
        self._snapshot_store.reset_invalid(
            capabilities,
            generation=self._state.generation(),
            captured_at=self._clock.time(),
        )

    def _recovery_requested(self) -> bool:
        with self._control_lock:
            ticket = self._recovery.ticket
            return (
                not self._performing_recovery
                and ticket is not None
                and not ticket.event.is_set()
            )

    def _auto_recovery_due(self) -> bool:
        with self._control_lock:
            due = self._recovery.due_at_s
        return (
            self._config.auto_reconnect
            and due is not None
            and due <= self._clock.monotonic()
        )

    def _recovery_deadline(self) -> float | None:
        if not self._config.auto_reconnect:
            return None
        with self._control_lock:
            return self._recovery.due_at_s

    def _make_runner(self) -> TransactionRunner:
        return TransactionRunner(
            binding=self._binding,
            backend=self._backend,
            endpoint_id=self._backend.endpoint_id,
            clock=self._clock,
            wake=self._wake,
            stop=self._stop,
            recovery_requested=self._recovery_requested,
            next_message=self._mailbox.pop,
            receiver_error=self._mailbox.error,
            report_error=lambda error: self._metrics.record_error(
                self._classify(error, "report")
            ),
            snapshot_store=self._snapshot_store,
            metrics=self._metrics,
        )

    def _wait_for_work(self) -> None:
        # Clear first, then inspect every work source. A producer racing any
        # later check leaves the event set, so the final wait cannot lose it.
        self._wake.clear()
        now = self._clock.monotonic()
        polling_deadline = self._polling.next_deadline()
        recovery_deadline = self._recovery_deadline()
        command_pending = self._commands.pending_count > 0
        command_ready = command_pending and now >= self._next_command_at
        polling_ready = polling_deadline is not None and polling_deadline <= now
        recovery_ready = recovery_deadline is not None and recovery_deadline <= now
        route_ready = self._state.ready()
        if (
            self._stop.is_set()
            or self._recovery_requested()
            or command_ready
            or polling_ready
            or recovery_ready
            or self._transactions.pending_count
            or (route_ready and self._mailbox.has_pending())
            or (route_ready and self._mailbox.error() is not None)
        ):
            return
        deadlines = tuple(
            value
            for value in (
                polling_deadline,
                recovery_deadline,
                self._next_command_at if command_pending else None,
            )
            if value is not None
        )
        timeout = None if not deadlines else max(0.0, min(deadlines) - now)
        self._clock.wait(self._wake, timeout)

    def _classify(self, error: BaseException, operation: str) -> HandError:
        return as_hand_error(
            error,
            operation=operation,
            model=self._state.declared.model,
            generation=self._state.generation(),
        )


def _connection_failure(error: HandError) -> bool:
    return isinstance(error, (TransportError, DeviceNotReadyError))


def _sent_frames(error: BaseException, fallback: int) -> int:
    current: BaseException | None = error
    while current is not None:
        transaction_sent_frames = getattr(current, "transaction_sent_frames", None)
        if isinstance(transaction_sent_frames, int):
            return transaction_sent_frames
        if isinstance(current, BatchDispatchError):
            return current.sent_frames
        current = getattr(current, "cause", None)
    return fallback


__all__ = ["DeviceActor"]
