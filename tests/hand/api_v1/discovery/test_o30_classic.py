"""O30 classic CAN discovery preserves wire mode, addressing and diagnostics."""

from dataclasses import replace

import pytest

from linkerbot_hand import (
    DeviceConfig,
    DiscoveryStatus,
    HandSide,
    TransportKind,
    ValidationError,
    discover_hands,
)
from tests.hand.api_v1.runtime.o30_canfd_fake import O30DeviceScript
from tests.hand.api_v1.runtime.o30_classic_fake import ClassicO30Transport

from .test_discovery import _FAST, _classic_channel, _Source


@pytest.mark.parametrize("bitrate", [1_000_000, 500_000])
@pytest.mark.parametrize("side", [HandSide.LEFT, None])
def test_discovery_preserves_classic_mode_custom_endpoint_and_actual_side(
    bitrate, side
):
    script = O30DeviceScript(
        product_model="O30", reported_side=side, request_id=0x123, response_id=0x523
    )
    source = _Source(
        _classic_channel(nominal_bitrate=bitrate),
        {TransportKind.CAN: lambda: ClassicO30Transport(script)},
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(0x123,)), source=source)
    assert not report.failures
    c = report.require_one(allow_partial=True)
    assert (
        c.wire_protocol is TransportKind.CAN and c.transport.kind is TransportKind.CAN
    )
    assert c.side is side
    assert c.status is (
        DiscoveryStatus.PARTIAL if side is None else DiscoveryStatus.VERIFIED
    )
    if bitrate != 1_000_000:
        assert all(len(m.data) >= 3 for _, t in source.created for m in t.requests)
    with pytest.raises(ValidationError, match="hop_transport"):
        c.create_hand_device(
            config=DeviceConfig(), side="right" if side is None else None
        )
    hand = c.create_hand_device(side="right" if side is None else None)
    try:
        hand.open()
        assert hand.get_capabilities().transport.kind is TransportKind.CAN
        assert hand.get_angles(timeout=1).raw == tuple(range(20))
    finally:
        hand.close()


def test_classic_discovery_preserves_device_error_instead_of_timing_out():
    class RejectInfo(O30DeviceScript):
        def _read(self, main_index, sub_index, length, *, rts):
            if main_index == 0x41 and sub_index == 0x10:
                return (self._error(0, 0x10),)
            return super()._read(main_index, sub_index, length, rts=rts)

    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ClassicO30Transport(RejectInfo())},
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(1,)), source=source)
    candidate = report.require_one(allow_partial=True)
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.problems[0].code != "identity_timeout"
    assert "permission denied" in candidate.problems[0].message


@pytest.mark.parametrize("first_si", [5, 10, 15])
def test_missing_first_product_fragment_keeps_partial_endpoint(first_si):
    class NoFirstProduct(O30DeviceScript):
        def _read(self, main_index, sub_index, length, *, rts):
            if main_index == 0x41:
                if sub_index != 0:
                    return ()
                return (
                    self._response(
                        main_index,
                        first_si,
                        self._identity_object(main_index)[first_si:16],
                    ),
                )
            return super()._read(main_index, sub_index, length, rts=rts)

    source = _Source(
        _classic_channel(),
        {TransportKind.CAN: lambda: ClassicO30Transport(NoFirstProduct())},
    )
    report = discover_hands(replace(_FAST, hop_request_ids=(1,)), source=source)
    candidate = report.require_one(allow_partial=True)
    assert candidate.status is DiscoveryStatus.PARTIAL
    assert candidate.model is None
    assert candidate.address.request_id == 1
    assert candidate.problems[0].code == "product_code_incomplete"
    assert not report.failures
