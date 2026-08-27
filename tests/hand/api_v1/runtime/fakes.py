from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from linkerbot_hand.api.commands import AckSupport
from linkerbot_hand.api.device_info import DeviceInfo, VersionField
from linkerbot_hand.api.enums import DataOrigin
from linkerbot_hand.api.readings import SampleMeta
from linkerbot_hand.capabilities.types import (
    CommandSemantic,
    DeviceCapabilities,
    TelemetrySemantic,
)
from linkerbot_hand.protocols import (
    IdentityResult,
    ProtocolTransaction,
    ResponseProgress,
    SamplePayload,
)
from linkerbot_transport import SendReceipt
from linkerbot_transport.testing import FakeTransport


@dataclass(frozen=True, slots=True)
class WireMessage:
    route: str
    kind: str
    semantic: str | None = None
    values: tuple[int | float, ...] = ()


@dataclass(slots=True)
class FakeProtocolState:
    capabilities: DeviceCapabilities
    fingerprint: str = "FAKE-L6-0001"
    readings: dict[TelemetrySemantic, tuple[int | float, ...]] = field(
        default_factory=dict
    )
    query_log: list[TelemetrySemantic] = field(default_factory=list)
    command_log: list[tuple[CommandSemantic, tuple[int | float, ...]]] = field(
        default_factory=list
    )
    operation_log: list[str] = field(default_factory=list)
    drop_reads: set[TelemetrySemantic] = field(default_factory=set)
    late_response_guard_s: float = 0.001
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.readings.setdefault(
            TelemetrySemantic.ANGLES,
            tuple(0 for _ in self.capabilities.joints),
        )

    def identity(self) -> IdentityResult:
        declared = self.capabilities
        verified = replace(
            declared,
            identity=replace(
                declared.identity,
                verified=True,
                firmware="2.4.0",
                serial_number=self.fingerprint,
                sensor_variant="fake",
            ),
        )
        info = DeviceInfo(
            model=verified.model,
            serial_number=self.fingerprint,
            pcb_version=None,
            firmware_version=VersionField(
                raw=(2, 4, 0),
                text="2.4.0",
                major=2,
                minor=4,
                patch=0,
            ),
            mechanical_version=None,
            product_code=f"{verified.model}-{self.fingerprint}",
            sensor_variant="fake",
            side=verified.side,
            raw_identity=self.fingerprint.encode(),
            meta=SampleMeta.unavailable(),
        )
        return IdentityResult(verified, info, self.fingerprint)


class FakeProtocolBackend:
    def __init__(
        self,
        state: FakeProtocolState,
        *,
        endpoint_id: str = "fake-hand",
        route_key: str = "fake-hand-rx",
    ) -> None:
        self.state = state
        self.endpoint_id = endpoint_id
        self.route_key = route_key

    def identity_transaction(
        self, capabilities: DeviceCapabilities
    ) -> ProtocolTransaction:
        del capabilities

        def consume(message: object) -> ResponseProgress:
            if not isinstance(message, WireMessage) or message.kind != "identity_reply":
                return ResponseProgress(matched=False)
            return ResponseProgress(
                matched=True,
                complete=True,
                result=self.state.identity(),
            )

        return ProtocolTransaction(
            key="identity",
            correlation_key="identity",
            request_messages=(WireMessage(self.endpoint_id, "identity_request"),),
            response_timeout_s=0.1,
            estimated_duration_s=0.001,
            response_consumer=consume,
            late_response_guard_s=self.state.late_response_guard_s,
        )

    def baseline_semantics(
        self, capabilities: DeviceCapabilities
    ) -> tuple[TelemetrySemantic, ...]:
        del capabilities
        return (TelemetrySemantic.ANGLES,)

    def command_transaction(
        self,
        semantic: CommandSemantic,
        values: tuple[int | float, ...],
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        capability = capabilities.command(semantic)
        assert capability is not None
        publications = ()
        if semantic is CommandSemantic.SPEEDS:
            publications = (
                SamplePayload(
                    semantic=TelemetrySemantic.CONFIGURED_SPEEDS,
                    raw=values,
                    origin=DataOrigin.COMMAND_CACHE,
                ),
            )
        response_consumer = None
        response_timeout_s = 0.0
        if capability.ack_support is AckSupport.SUPPORTED:

            def consume(message: object) -> ResponseProgress:
                if (
                    not isinstance(message, WireMessage)
                    or message.kind != "command_ack"
                    or message.semantic != semantic.value
                ):
                    return ResponseProgress(matched=False)
                return ResponseProgress(
                    matched=True,
                    complete=True,
                    acknowledged=True,
                )

            response_consumer = consume
            response_timeout_s = 0.1
        return ProtocolTransaction(
            key=("command", semantic),
            correlation_key=("command", semantic),
            request_messages=(
                WireMessage(self.endpoint_id, "command", semantic.value, values),
            ),
            response_timeout_s=response_timeout_s,
            estimated_duration_s=0.001,
            response_consumer=response_consumer,
            dispatch_publications=publications,
        )

    def read_transaction(
        self,
        semantic: TelemetrySemantic,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        del capabilities

        def consume(message: object) -> ResponseProgress:
            if (
                not isinstance(message, WireMessage)
                or message.kind != "read_reply"
                or message.semantic != semantic.value
            ):
                return ResponseProgress(matched=False)
            return ResponseProgress(
                matched=True,
                complete=True,
                result=SamplePayload(semantic=semantic, raw=message.values),
            )

        return ProtocolTransaction(
            key=("read", semantic),
            correlation_key=("read", semantic),
            request_messages=(WireMessage(self.endpoint_id, "read", semantic.value),),
            response_timeout_s=0.1,
            estimated_duration_s=0.001,
            response_consumer=consume,
            late_response_guard_s=self.state.late_response_guard_s,
        )

    def operation_transaction(
        self,
        operation: str,
        capabilities: DeviceCapabilities,
    ) -> ProtocolTransaction:
        del capabilities
        return ProtocolTransaction(
            key=("operation", operation),
            correlation_key=("operation", operation),
            request_messages=(WireMessage(self.endpoint_id, "operation", operation),),
            response_timeout_s=0,
            estimated_duration_s=0.001,
        )

    def decode_report(
        self,
        message: object,
        capabilities: DeviceCapabilities,
    ) -> tuple[SamplePayload, ...]:
        del capabilities
        if not isinstance(message, WireMessage) or message.kind not in {
            "read_reply",
            "report",
        }:
            return ()
        semantic = TelemetrySemantic(message.semantic)
        return (SamplePayload(semantic=semantic, raw=message.values),)

    def responses_for(self, message: WireMessage) -> tuple[WireMessage, ...]:
        with self.state.lock:
            if message.kind == "identity_request":
                return (WireMessage(self.route_key, "identity_reply"),)
            if message.kind == "read":
                assert message.semantic is not None
                semantic = TelemetrySemantic(message.semantic)
                self.state.query_log.append(semantic)
                if semantic in self.state.drop_reads:
                    return ()
                values = self.state.readings[semantic]
                return (
                    WireMessage(
                        self.route_key,
                        "read_reply",
                        semantic.value,
                        values,
                    ),
                )
            if message.kind == "command":
                assert message.semantic is not None
                semantic = CommandSemantic(message.semantic)
                self.state.command_log.append((semantic, message.values))
                capability = self.state.capabilities.command(semantic)
                assert capability is not None
                if capability.ack_support is AckSupport.SUPPORTED:
                    return (
                        WireMessage(
                            self.route_key,
                            "command_ack",
                            semantic.value,
                        ),
                    )
            elif message.kind == "operation":
                self.state.operation_log.append(message.semantic or "")
            return ()


class ScriptedFakeTransport(FakeTransport[WireMessage]):
    def __init__(self, *backends: FakeProtocolBackend) -> None:
        if not backends:
            raise ValueError("at least one fake protocol backend is required")
        super().__init__(route_key_of=lambda message: message.route)
        self.backends = {backend.endpoint_id: backend for backend in backends}
        if len(self.backends) != len(backends):
            raise ValueError("fake protocol endpoint IDs must be unique")
        self.command_gate = threading.Event()
        self.command_gate.set()
        self.command_send_started = threading.Event()
        self.read_gate = threading.Event()
        self.read_gate.set()
        self.read_send_started = threading.Event()
        self.recover_gate = threading.Event()
        self.recover_gate.set()
        self.recover_started = threading.Event()
        self.recover_error_after_open: BaseException | None = None
        self.command_sent_hook: Callable[[], None] | None = None

    def recover(self) -> None:
        self.recover_started.set()
        if not self.recover_gate.wait(timeout=2):
            raise RuntimeError("fake transport recovery gate timed out")
        super().recover()
        if self.recover_error_after_open is not None:
            raise self.recover_error_after_open

    def send(self, message: WireMessage) -> SendReceipt:
        if message.kind == "command":
            self.command_send_started.set()
            self.command_gate.wait(timeout=2)
        if message.kind == "read":
            self.read_send_started.set()
            self.read_gate.wait(timeout=2)
        receipt = super().send(message)
        if receipt.exception() is None:
            backend = self.backends[message.route]
            for response in backend.responses_for(message):
                self.emit(response)
            if message.kind == "command" and self.command_sent_hook is not None:
                self.command_sent_hook()
        return receipt


def wait_until(predicate, *, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition not met")
        time.sleep(0.001)
