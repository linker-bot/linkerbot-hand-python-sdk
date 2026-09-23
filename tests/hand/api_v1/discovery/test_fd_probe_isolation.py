"""One model's failed request must not conceal later CAN FD endpoints."""

import errno

import pytest

from linkerbot_hand import TransportKind, discover_hands
from linkerbot_transport import SendReceipt, TransportState
from tests.hand.api_v1.runtime.l30_canfd_fake import (
    L30DeviceScript,
    ScriptedL30Transport,
)
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)

from .test_discovery import _FAST, _empty_transport, _fd_channel, _Source


class FailingProbeTransport(ScriptedO30Transport):
    def __init__(
        self,
        *,
        fail_o20=True,
        fail_l30=False,
        close_on_failure=False,
        error=None,
        failed_receipt=False,
    ):
        super().__init__(O30DeviceScript())
        self.fail_o20 = fail_o20
        self.fail_l30 = fail_l30
        self.close_on_failure = close_on_failure
        self.error = error or ValueError("model-specific request rejected")
        self.failed_receipt = failed_receipt
        self.attempts = []

    def send(self, message):
        self.attempts.append(message)
        # O20 register-ID frame and L30 host/node frame are both extended;
        # the first batch is O20 and the second L30. Use their actual IDs.
        from linkerbot_hand import HandSide
        from linkerbot_hand.protocols.o20.codec import (
            REG_DEVICE_INFO,
            device_id_for_side,
            read_route,
        )

        o20_ids = {
            read_route(device_id_for_side(side), REG_DEVICE_INFO).arbitration_id
            for side in (HandSide.LEFT, HandSide.RIGHT)
        }
        is_o20 = message.is_extended_id and message.arbitration_id in o20_ids
        is_l30 = message.is_extended_id and not is_o20
        if (is_o20 and self.fail_o20) or (is_l30 and self.fail_l30):
            if self.close_on_failure:
                self.close()
            if self.failed_receipt:
                return SendReceipt.failed(self.error)
            raise self.error
        return super().send(message)


def run(transport):
    source = _Source(
        _fd_channel(data_bitrate=5_000_000),
        {
            TransportKind.CAN: _empty_transport,
            TransportKind.CAN_FD: lambda: transport,
        },
    )
    return discover_hands(_FAST, source=source)


@pytest.mark.parametrize(
    ("fail_o20", "fail_l30"), [(True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("failed_receipt", [False, True])
def test_model_failure_keeps_later_o30_probe(fail_o20, fail_l30, failed_receipt):
    transport = FailingProbeTransport(
        fail_o20=fail_o20, fail_l30=fail_l30, failed_receipt=failed_receipt
    )
    report = run(transport)
    assert report.require_one().model == "O30"
    expected = []
    if fail_o20:
        expected.append("probe_o20_identity")
    # O30 is now checked before L30; a verified single-hand channel stops here.
    assert [failure.operation for failure in report.failures] == expected
    assert all(failure.error_type == "ValueError" for failure in report.failures)
    assert transport.get_health().state is TransportState.CLOSED


@pytest.mark.parametrize("close_on_failure", [False, True])
@pytest.mark.parametrize("failed_receipt", [False, True])
def test_dead_link_does_not_send_later_model_probes(close_on_failure, failed_receipt):
    transport = FailingProbeTransport(
        close_on_failure=close_on_failure,
        error=OSError(errno.ENETDOWN, "CAN link down"),
        failed_receipt=failed_receipt,
    )
    report = run(transport)
    assert not report.candidates
    assert len(transport.attempts) == 1
    assert len(report.failures) == 1
    assert transport.get_health().state is TransportState.CLOSED


def test_closed_link_after_non_transport_exception_is_still_stopped():
    transport = FailingProbeTransport(close_on_failure=True)
    report = run(transport)
    assert not report.candidates
    assert len(transport.attempts) == 1
    assert len(report.failures) == 1


@pytest.mark.parametrize("failed_receipt", [False, True])
def test_o20_and_o30_send_failures_keep_l30_probe(failed_receipt):
    class L30OnlyTransport(ScriptedL30Transport):
        def send(self, message):
            # Real L30 request IDs are above 0x600000; O20 and O30 requests
            # are independently rejected while the adapter stays healthy.
            if message.arbitration_id < 0x600000:
                error = ValueError("model-specific request rejected")
                if failed_receipt:
                    return SendReceipt.failed(error)
                raise error
            return super().send(message)

    transport = L30OnlyTransport(L30DeviceScript())
    report = run(transport)
    assert report.require_one().model == "L30"
    assert [f.operation for f in report.failures] == [
        "probe_o20_identity",
        "probe_o30_identity",
    ]
    assert transport.get_health().state is TransportState.CLOSED


def test_fatal_error_during_full_o30_identity_stops_other_addresses():
    class LostO30Transport(ScriptedO30Transport):
        def send(self, message):
            if not message.is_extended_id and message.data[:2] == b"\x41\x10":
                return SendReceipt.failed(OSError(errno.ENODEV, "adapter unplugged"))
            return super().send(message)

    transport = LostO30Transport(O30DeviceScript())
    report = run(transport)
    assert not report.verified_candidates
    assert [f.operation for f in report.failures] == ["probe_o30_identity"]
    assert not any(message.arbitration_id == 2 for message in transport.sent)
    assert transport.get_health().state is TransportState.CLOSED
