"""Actor-thread execution of one protocol transaction at a time."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from linkerbot_transport import SendOutcome, SendReceipt

from ..api.errors import ClosedError, DeviceNotReadyError, ProtocolError, TimeoutError
from ..capabilities.types import DeviceCapabilities, TelemetrySemantic
from ..protocols.base import (
    IdentityResult,
    ProtocolBackend,
    ProtocolTransaction,
    ResponseProgress,
    SamplePayload,
)
from ._clock import RuntimeClock
from ._route_mailbox import InboundMessage
from ._transport_binding import TransportBinding
from .metrics import RuntimeMetrics
from .snapshot_store import SnapshotStore


class TransactionSuperseded(Exception):
    """Internal signal that no frame in a replaceable batch was sent."""


@dataclass(frozen=True, slots=True)
class TransactionOutcome:
    """Decoded result and publications from one complete transaction."""

    result: IdentityResult | SamplePayload | None
    acknowledged: bool
    publications: tuple[tuple[SamplePayload, object], ...]
    sent_frames: int


@dataclass(frozen=True, slots=True)
class _StageOutcome:
    """Internal result of one dispatched stage in a transaction chain."""

    result: IdentityResult | SamplePayload | None
    acknowledged: bool
    publications: tuple[tuple[SamplePayload, object], ...]
    sent_frames: int


@dataclass(frozen=True, slots=True)
class PendingTransaction:
    """A protocol request accepted by transport but not yet completed."""

    transaction: ProtocolTransaction
    receipt: SendReceipt
    capabilities: DeviceCapabilities
    publish_generation: int | None
    started_at_s: float
    deadline_at_s: float | None
    priority: int


@dataclass(frozen=True, slots=True)
class _QuarantinedTransaction:
    transaction: ProtocolTransaction
    deadline_at_s: float


@dataclass(slots=True)
class _BufferedResponse:
    """Response progress received before transport delivery is published."""

    progress: ResponseProgress | None = None
    publication_batches: tuple[tuple[SamplePayload, ...], ...] = ()
    error: BaseException | None = None


class TransactionRunner:
    """Dispatch request batches and feed routed responses to model codecs."""

    def __init__(
        self,
        *,
        binding: TransportBinding,
        backend: ProtocolBackend,
        endpoint_id: Hashable,
        clock: RuntimeClock,
        wake: threading.Event,
        stop: threading.Event,
        recovery_requested: Callable[[], bool],
        next_message: Callable[[], InboundMessage | None],
        receiver_error: Callable[[], BaseException | None],
        report_error: Callable[[BaseException], None],
        snapshot_store: SnapshotStore,
        metrics: RuntimeMetrics,
    ) -> None:
        self._binding = binding
        self._backend = backend
        self._endpoint_id = endpoint_id
        self._clock = clock
        self._wake = wake
        self._stop = stop
        self._recovery_requested = recovery_requested
        self._next_message = next_message
        self._receiver_error = receiver_error
        self._report_error = report_error
        self._snapshot_store = snapshot_store
        self._metrics = metrics
        self._quarantined: dict[Hashable, _QuarantinedTransaction] = {}

    def execute(
        self,
        transaction: ProtocolTransaction,
        *,
        capabilities: DeviceCapabilities,
        publish_generation: int | None,
        latest_family: Hashable | None,
        priority: int,
        deadline_at_s: float | None = None,
        on_dispatched: Callable[[float, int], None] | None = None,
        on_stage_boundary: Callable[[float], None] | None = None,
        require_published_semantic: TelemetrySemantic | None = None,
    ) -> TransactionOutcome:
        """Execute one transaction without allowing another correlation conflict."""

        pending = self.begin(
            transaction,
            capabilities=capabilities,
            publish_generation=publish_generation,
            latest_family=latest_family,
            priority=priority,
            deadline_at_s=deadline_at_s,
        )
        return self.complete(
            pending,
            on_dispatched=on_dispatched,
            on_stage_boundary=on_stage_boundary,
            require_published_semantic=require_published_semantic,
        )

    def begin(
        self,
        transaction: ProtocolTransaction,
        *,
        capabilities: DeviceCapabilities,
        publish_generation: int | None,
        latest_family: Hashable | None,
        priority: int,
        deadline_at_s: float | None = None,
    ) -> PendingTransaction:
        """Submit a transaction and return before its transport receipt completes."""

        self._wait_for_quarantine(
            transaction.correlation_key,
            capabilities=capabilities,
            generation=publish_generation,
        )
        started = self._clock.monotonic()
        receipt = self._binding.submit(
            transaction.request_messages,
            request_interval_s=transaction.request_interval_s,
            endpoint_id=self._endpoint_id,
            latest_family=latest_family,
            priority=priority,
            deadline_at_s=deadline_at_s,
        )
        receipt.add_done_callback(lambda _receipt: self._wake.set())
        return PendingTransaction(
            transaction=transaction,
            receipt=receipt,
            capabilities=capabilities,
            publish_generation=publish_generation,
            started_at_s=started,
            deadline_at_s=deadline_at_s,
            priority=priority,
        )

    def replace_pending(
        self,
        transaction: ProtocolTransaction,
        *,
        capabilities: DeviceCapabilities,
        publish_generation: int | None,
        latest_family: Hashable,
        priority: int,
        deadline_at_s: float | None = None,
    ) -> PendingTransaction | None:
        """Replace a still-unclaimed keyed request without adding a new job."""

        started = self._clock.monotonic()
        receipt = self._binding.replace_latest_if_pending(
            transaction.request_messages,
            request_interval_s=transaction.request_interval_s,
            endpoint_id=self._endpoint_id,
            latest_family=latest_family,
            priority=priority,
            deadline_at_s=deadline_at_s,
        )
        if receipt is None:
            return None
        receipt.add_done_callback(lambda _receipt: self._wake.set())
        return PendingTransaction(
            transaction=transaction,
            receipt=receipt,
            capabilities=capabilities,
            publish_generation=publish_generation,
            started_at_s=started,
            deadline_at_s=deadline_at_s,
            priority=priority,
        )

    def complete(
        self,
        pending: PendingTransaction,
        *,
        on_dispatched: Callable[[float, int], None] | None = None,
        on_stage_boundary: Callable[[float], None] | None = None,
        require_published_semantic: TelemetrySemantic | None = None,
    ) -> TransactionOutcome:
        """Finish every response-gated stage and publish canonical samples.

        ``require_published_semantic`` makes "the caller actually got its data"
        part of the transaction's success condition. It has to be checked here
        rather than by the caller: ``transaction_completed`` below clears the
        consecutive-error counter, so a check made after this returns would be
        counted only after the counter had already been reset.
        """

        started_at_s = pending.started_at_s
        sent_frames = 0
        publications: tuple[tuple[SamplePayload, object], ...] = ()
        result: IdentityResult | SamplePayload | None = None
        acknowledged = False
        current = pending

        while True:
            transaction = current.transaction
            final_stage = (
                transaction.next_transaction is None
                and transaction.next_transaction_factory is None
            )

            def mark_final_dispatched(
                timestamp: float,
                stage_sent_frames: int,
                prior_sent_frames: int = sent_frames,
            ) -> None:
                if on_dispatched is not None:
                    on_dispatched(
                        timestamp,
                        prior_sent_frames + stage_sent_frames,
                    )

            try:
                outcome = self._complete_stage(
                    current,
                    on_dispatched=(mark_final_dispatched if final_stage else None),
                )
            except BaseException as error:
                _record_sent_frames(error, sent_frames)
                raise

            sent_frames += outcome.sent_frames
            publications += outcome.publications
            if outcome.result is not None:
                result = outcome.result
            acknowledged = outcome.acknowledged
            try:
                next_transaction = transaction.build_next_transaction()
            except BaseException as error:
                _record_sent_frames(error, sent_frames)
                raise
            if next_transaction is None:
                if require_published_semantic is not None and not any(
                    payload.semantic is require_published_semantic
                    for payload, _sample in publications
                ):
                    error = ProtocolError(
                        "read transaction did not publish "
                        f"{require_published_semantic.value}"
                    )
                    _record_sent_frames(error, sent_frames)
                    raise error
                completed_at = self._clock.monotonic()
                self._metrics.transaction_completed(
                    timestamp=completed_at,
                    duration_s=max(0.0, completed_at - started_at_s),
                )
                return TransactionOutcome(
                    result,
                    acknowledged,
                    publications,
                    sent_frames,
                )

            if transaction.yield_after_response and on_stage_boundary is not None:
                try:
                    on_stage_boundary(next_transaction.estimated_duration_s)
                except BaseException as error:
                    _record_sent_frames(error, sent_frames)
                    raise

            try:
                current = self.begin(
                    next_transaction,
                    capabilities=current.capabilities,
                    publish_generation=current.publish_generation,
                    latest_family=None,
                    priority=current.priority,
                    deadline_at_s=current.deadline_at_s,
                )
            except BaseException as error:
                _record_sent_frames(error, sent_frames)
                raise

    def _complete_stage(
        self,
        pending: PendingTransaction,
        *,
        on_dispatched: Callable[[float, int], None] | None,
    ) -> _StageOutcome:
        """Finish one stage without exposing a whole-command dispatch early."""

        transaction = pending.transaction
        receipt = pending.receipt
        sent_frames = 0
        try:
            buffered = self._wait_for_receipt(pending)
            result = receipt.result()
            if result.outcome is SendOutcome.SUPERSEDED:
                raise TransactionSuperseded
            if result.outcome is not SendOutcome.DISPATCHED:
                raise ProtocolError(f"send ended as {result.outcome.value}")
            dispatched_at = result.completed_at_s
            sent_frames = len(transaction.request_messages)
            self._metrics.sent(timestamp=dispatched_at)
            if on_dispatched is not None:
                on_dispatched(dispatched_at, sent_frames)

            publications = self._publish(
                transaction.dispatch_publications,
                generation=pending.publish_generation,
            )
            if not transaction.expects_response:
                return _StageOutcome(
                    None,
                    False,
                    publications,
                    sent_frames,
                )
            if buffered.error is not None:
                raise buffered.error
            for payloads in buffered.publication_batches:
                publications += self._publish(
                    _deduplicate_payloads(payloads),
                    generation=pending.publish_generation,
                )

            response_deadline = dispatched_at + transaction.response_timeout_s
            try:
                progress = buffered.progress
                if progress is None or not progress.complete:
                    progress, response_publications = self._wait_for_response(
                        transaction,
                        capabilities=pending.capabilities,
                        generation=pending.publish_generation,
                        deadline_s=response_deadline,
                    )
                    publications += response_publications
            except TimeoutError:
                if transaction.late_response_guard_s > 0:
                    self._quarantined[transaction.correlation_key] = (
                        _QuarantinedTransaction(
                            transaction=transaction,
                            deadline_at_s=(
                                self._clock.monotonic()
                                + transaction.late_response_guard_s
                            ),
                        )
                    )
                raise
            if progress.acknowledged != transaction.response_acknowledges_command:
                raise ProtocolError(
                    "response acknowledgement disagrees with transaction contract"
                )
            return _StageOutcome(
                progress.result,
                progress.acknowledged,
                publications,
                sent_frames,
            )
        except BaseException as error:
            _record_sent_frames(error, sent_frames)
            raise

    def process_unsolicited(
        self,
        inbound: InboundMessage,
        *,
        capabilities: DeviceCapabilities,
        generation: int | None,
    ) -> None:
        """Decode report traffic only when no request transaction owns it."""

        self._metrics.received(timestamp=inbound.received_at_s)
        if self._consume_quarantined(inbound.message):
            return
        self._publish_report(inbound, capabilities=capabilities, generation=generation)

    def _publish_report(
        self,
        inbound: InboundMessage,
        *,
        capabilities: DeviceCapabilities,
        generation: int | None,
    ) -> None:
        """Decode and publish report traffic behind a backend error boundary.

        ``ProtocolBackend.decode_report`` must return an empty tuple when a
        message is unmatched, but a backend that raises instead must never fail
        whichever transaction happens to be in flight.
        """

        try:
            payloads = self._backend.decode_report(inbound.message, capabilities)
        except Exception as error:  # noqa: BLE001 - report callback boundary
            self._report_error(error)
            return
        self._publish(payloads, generation=generation)

    def _wait_for_receipt(
        self,
        pending: PendingTransaction,
    ) -> _BufferedResponse:
        receipt = pending.receipt
        transaction = pending.transaction
        deadline_at_s = pending.deadline_at_s
        buffered = _BufferedResponse()
        while not receipt.done():
            self.raise_if_interrupted()
            now = self._clock.monotonic()
            if deadline_at_s is not None and now >= deadline_at_s:
                raise TimeoutError("transport queue deadline expired")
            remaining = None if deadline_at_s is None else max(0.0, deadline_at_s - now)
            self._wake.clear()
            if receipt.done():
                break
            if self._process_before_receipt(
                transaction,
                buffered,
                capabilities=pending.capabilities,
                generation=pending.publish_generation,
            ):
                continue
            self.raise_if_interrupted()
            self._clock.wait(self._wake, remaining)
        return buffered

    def _process_before_receipt(
        self,
        transaction: ProtocolTransaction,
        buffered: _BufferedResponse,
        *,
        capabilities: DeviceCapabilities,
        generation: int | None,
    ) -> bool:
        inbound = self._next_message()
        if inbound is None:
            return False
        self._metrics.received(timestamp=inbound.received_at_s)
        if self._consume_quarantined(inbound.message):
            return True
        if (
            transaction.expects_response
            and buffered.error is None
            and (buffered.progress is None or not buffered.progress.complete)
        ):
            try:
                progress = transaction.consume(inbound.message)
            except Exception as error:  # noqa: BLE001 - defer until DISPATCHED
                buffered.error = error
                return True
            if progress.matched:
                payloads = progress.publications
                if isinstance(progress.result, SamplePayload):
                    payloads += (progress.result,)
                buffered.progress = progress
                buffered.publication_batches += (payloads,)
                return True
        try:
            self._publish(
                self._backend.decode_report(inbound.message, capabilities),
                generation=generation,
            )
        except Exception as error:  # noqa: BLE001 - report callback boundary
            self._report_error(error)
        return True

    def _wait_for_response(
        self,
        transaction: ProtocolTransaction,
        *,
        capabilities: DeviceCapabilities,
        generation: int | None,
        deadline_s: float,
    ) -> tuple[ResponseProgress, tuple[tuple[SamplePayload, object], ...]]:
        publications: tuple[tuple[SamplePayload, object], ...] = ()
        while True:
            self.raise_if_interrupted()
            self._wake.clear()
            inbound = self._next_message()
            if inbound is not None:
                self._metrics.received(timestamp=inbound.received_at_s)
                if self._consume_quarantined(inbound.message):
                    continue
                progress = transaction.consume(inbound.message)
                if not progress.matched:
                    if generation is not None:
                        self._publish_report(
                            inbound,
                            capabilities=capabilities,
                            generation=generation,
                        )
                    continue
                payloads = progress.publications
                if isinstance(progress.result, SamplePayload):
                    payloads += (progress.result,)
                publications += self._publish(
                    _deduplicate_payloads(payloads),
                    generation=generation,
                )
                if progress.complete:
                    return progress, publications
                continue
            remaining = deadline_s - self._clock.monotonic()
            if remaining <= 0:
                raise TimeoutError("protocol response did not complete before timeout")
            self.raise_if_interrupted()
            self._clock.wait(self._wake, remaining)

    def _publish(
        self,
        payloads: tuple[SamplePayload, ...],
        *,
        generation: int | None,
    ) -> tuple[tuple[SamplePayload, object], ...]:
        if generation is None:
            return ()
        published: list[tuple[SamplePayload, object]] = []
        for payload in payloads:
            now = self._clock.monotonic()
            sample = self._snapshot_store.publish(
                payload,
                generation=generation,
                collected_at=self._clock.time(),
                collected_monotonic=now,
            )
            published.append((payload, sample))
        return tuple(published)

    def _wait_for_quarantine(
        self,
        correlation_key: Hashable,
        *,
        capabilities: DeviceCapabilities,
        generation: int | None,
    ) -> None:
        while (quarantined := self._active_quarantine(correlation_key)) is not None:
            self.raise_if_interrupted()
            self._wake.clear()
            while (inbound := self._next_message()) is not None:
                self.process_unsolicited(
                    inbound,
                    capabilities=capabilities,
                    generation=generation,
                )
                if correlation_key not in self._quarantined:
                    return
            remaining = max(
                0.0,
                quarantined.deadline_at_s - self._clock.monotonic(),
            )
            self.raise_if_interrupted()
            self._clock.wait(self._wake, remaining)

    def _active_quarantine(
        self, correlation_key: Hashable
    ) -> _QuarantinedTransaction | None:
        quarantined = self._quarantined.get(correlation_key)
        if (
            quarantined is not None
            and self._clock.monotonic() >= quarantined.deadline_at_s
        ):
            del self._quarantined[correlation_key]
            return None
        return quarantined

    def _consume_quarantined(self, message: object) -> bool:
        now = self._clock.monotonic()
        for correlation_key, quarantined in tuple(self._quarantined.items()):
            if now >= quarantined.deadline_at_s:
                del self._quarantined[correlation_key]
                continue
            try:
                progress = quarantined.transaction.consume(message)
            except Exception as error:  # noqa: BLE001 - quarantine parse boundary
                # A quarantined transaction's parse failure must never be
                # attributed to whichever transaction is in flight now — the
                # same invariant _publish_report holds for decode_report. Drop
                # the entry as well: keeping it would re-raise on every later
                # frame for the rest of the guard window, and would block the
                # next transaction that shares this correlation key.
                del self._quarantined[correlation_key]
                self._report_error(error)
                return True
            if not progress.matched:
                continue
            if progress.complete:
                del self._quarantined[correlation_key]
            return True
        return False

    def raise_if_interrupted(self) -> None:
        """Raise when lifecycle control invalidates the active transaction."""

        if self._stop.is_set():
            raise ClosedError("device runtime is stopping")
        if self._recovery_requested():
            raise DeviceNotReadyError("device recovery was requested")
        receiver_error = self._receiver_error()
        if receiver_error is not None:
            raise receiver_error


def _deduplicate_payloads(
    payloads: tuple[SamplePayload, ...],
) -> tuple[SamplePayload, ...]:
    result: list[SamplePayload] = []
    for payload in payloads:
        if payload not in result:
            result.append(payload)
    return tuple(result)


def _record_sent_frames(error: BaseException, prior_frames: int) -> None:
    """Attach a cumulative count without obscuring the original error type."""

    current_frames = getattr(error, "transaction_sent_frames", None)
    if current_frames is None:
        current_frames = getattr(error, "sent_frames", 0)
    try:
        error.transaction_sent_frames = (  # ty: ignore[unresolved-attribute]
            prior_frames + current_frames
        )
    except (AttributeError, TypeError):
        pass


__all__ = [
    "PendingTransaction",
    "TransactionOutcome",
    "TransactionRunner",
    "TransactionSuperseded",
]
