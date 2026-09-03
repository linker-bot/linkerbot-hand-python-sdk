from __future__ import annotations

import pytest

from linkerbot_hand.api.enums import DataOrigin
from linkerbot_hand.api.errors import ValidationError
from linkerbot_hand.capabilities.types import TelemetrySemantic
from linkerbot_hand.protocols import (
    ProtocolTransaction,
    ResponseProgress,
    SamplePayload,
)


def test_response_transaction_keeps_codec_logic_out_of_runtime() -> None:
    seen: list[object] = []

    def consume(message: object) -> ResponseProgress:
        seen.append(message)
        if message != b"response":
            return ResponseProgress(matched=False)
        payload = SamplePayload(
            semantic=TelemetrySemantic.ANGLES,
            raw=(1, 2, 3),
        )
        return ResponseProgress(matched=True, complete=True, result=payload)

    transaction = ProtocolTransaction(
        key="read-angles",
        correlation_key=0x01,
        request_messages=(b"request",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=consume,
    )

    assert transaction.expects_response
    assert not transaction.consume(b"other").matched
    progress = transaction.consume(b"response")
    assert progress.complete
    assert isinstance(progress.result, SamplePayload)
    assert seen == [b"other", b"response"]


def test_dispatch_only_transaction_can_publish_command_cache() -> None:
    payload = SamplePayload(
        semantic=TelemetrySemantic.CONFIGURED_SPEEDS,
        raw=(80,) * 6,
        origin=DataOrigin.COMMAND_CACHE,
    )
    transaction = ProtocolTransaction(
        key="set-speed",
        correlation_key=0x05,
        request_messages=(b"speed",),
        response_timeout_s=0,
        estimated_duration_s=0.001,
        dispatch_publications=(payload,),
    )

    assert not transaction.expects_response
    assert transaction.dispatch_publications == (payload,)
    assert not transaction.consume(b"echo").matched


def test_protocol_contract_rejects_empty_or_incoherent_results() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        SamplePayload(semantic=TelemetrySemantic.ANGLES)
    with pytest.raises(ValidationError, match="unmatched"):
        ResponseProgress(matched=False, complete=True)
    with pytest.raises(ValidationError, match="positive timeout"):
        ProtocolTransaction(
            key="identity",
            correlation_key="identity",
            request_messages=(b"request",),
            response_timeout_s=0,
            estimated_duration_s=0.01,
            response_consumer=lambda _message: ResponseProgress(matched=False),
        )


def test_staged_transaction_requires_a_response_gate() -> None:
    final = ProtocolTransaction(
        key="final",
        correlation_key="final",
        request_messages=(b"final",),
        response_timeout_s=0,
        estimated_duration_s=0.01,
    )

    with pytest.raises(ValidationError, match="must await a response"):
        ProtocolTransaction(
            key="first",
            correlation_key="first",
            request_messages=(b"first",),
            response_timeout_s=0,
            estimated_duration_s=0.01,
            next_transaction=final,
        )

    first = ProtocolTransaction(
        key="first",
        correlation_key="first",
        request_messages=(b"first",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=lambda _message: ResponseProgress(
            matched=True,
            complete=True,
        ),
        next_transaction=final,
    )

    assert first.next_transaction is final
    assert not first.terminal_expects_response
    assert not first.terminal_acknowledges_command


def test_stage_yield_requires_a_following_response_gated_stage() -> None:
    with pytest.raises(
        ValidationError,
        match="yield_after_response requires a following transaction stage",
    ):
        ProtocolTransaction(
            key="stage",
            correlation_key="stage",
            request_messages=(object(),),
            response_timeout_s=0.1,
            estimated_duration_s=0.1,
            response_consumer=lambda _message: ResponseProgress(matched=False),
            yield_after_response=True,
        )


def test_dynamic_stage_is_resolved_only_after_the_response_boundary() -> None:
    final = ProtocolTransaction(
        key="final",
        correlation_key="final",
        request_messages=(b"final",),
        response_timeout_s=0,
        estimated_duration_s=0.01,
    )
    selected = False

    def build_next() -> ProtocolTransaction | None:
        nonlocal selected
        selected = True
        return final

    first = ProtocolTransaction(
        key="first",
        correlation_key="first",
        request_messages=(b"first",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=lambda _message: ResponseProgress(
            matched=True,
            complete=True,
        ),
        next_transaction_factory=build_next,
    )

    assert first.has_dynamic_next_transaction
    assert not selected
    assert first.build_next_transaction() is final
    assert selected
    assert first.terminal_expects_response
    assert not first.terminal_acknowledges_command


def test_dynamic_stage_rejects_an_invalid_factory_result() -> None:
    transaction = ProtocolTransaction(
        key="dynamic",
        correlation_key="dynamic",
        request_messages=(b"dynamic",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=lambda _message: ResponseProgress(matched=False),
        next_transaction_factory=lambda: "not-a-transaction",  # type: ignore[return-value]
    )

    with pytest.raises(ValidationError, match="ProtocolTransaction or None"):
        transaction.build_next_transaction()


def test_response_wait_and_command_ack_are_independent_contracts() -> None:
    echo = ProtocolTransaction(
        key="angle-echo",
        correlation_key="angle-echo",
        request_messages=(b"command",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=lambda _message: ResponseProgress(
            matched=True,
            complete=True,
            result=SamplePayload(
                semantic=TelemetrySemantic.ANGLES,
                raw=(1, 2, 3),
                origin=DataOrigin.DEVICE_COMMAND_FEEDBACK,
            ),
        ),
    )
    ack = ProtocolTransaction(
        key="ack",
        correlation_key="ack",
        request_messages=(b"command",),
        response_timeout_s=0.1,
        estimated_duration_s=0.01,
        response_consumer=lambda _message: ResponseProgress(
            matched=True,
            complete=True,
            acknowledged=True,
        ),
        response_acknowledges_command=True,
    )

    assert echo.expects_response
    assert not echo.terminal_acknowledges_command
    assert ack.expects_response
    assert ack.terminal_acknowledges_command

    with pytest.raises(ValidationError, match="must await a response"):
        ProtocolTransaction(
            key="invalid-ack",
            correlation_key="invalid-ack",
            request_messages=(b"command",),
            response_timeout_s=0,
            estimated_duration_s=0.01,
            response_acknowledges_command=True,
        )
