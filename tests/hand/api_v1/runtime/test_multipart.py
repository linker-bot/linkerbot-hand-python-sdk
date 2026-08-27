from __future__ import annotations

import pytest

from linkerbot_hand.api.errors import ProtocolError
from linkerbot_hand.runtime.multipart import FragmentMarkerMode, MultipartAssembler


def make_assembler(mode: FragmentMarkerMode) -> MultipartAssembler:
    return MultipartAssembler(
        total_size=8,
        fragment_size=2,
        marker_mode=mode,
        generation=3,
        source_id=0x27,
        transaction_token="read-1",
    )


@pytest.mark.parametrize(
    ("mode", "markers"),
    [
        (FragmentMarkerMode.INDEX, (0, 1, 2, 3)),
        (FragmentMarkerMode.BYTE_OFFSET, (0, 2, 4, 6)),
    ],
)
def test_fragment_indices_and_byte_offsets_both_assemble(
    mode: FragmentMarkerMode, markers: tuple[int, ...]
) -> None:
    assembler = make_assembler(mode)
    fragments = (b"AB", b"CD", b"EF", b"GH")

    for marker, payload in reversed(tuple(zip(markers, fragments, strict=True))):
        assembler.add(
            marker,
            payload,
            generation=3,
            source_id=0x27,
            transaction_token="read-1",
        )

    assert assembler.complete
    assert assembler.result() == b"ABCDEFGH"
    assert assembler.missing_ranges() == ()


def test_identical_duplicates_are_accepted_but_conflicts_fail() -> None:
    assembler = make_assembler(FragmentMarkerMode.INDEX)
    options = {
        "generation": 3,
        "source_id": 0x27,
        "transaction_token": "read-1",
    }

    assert not assembler.add(0, b"AB", **options)
    assert not assembler.add(0, b"AB", **options)
    with pytest.raises(ProtocolError, match="conflicts"):
        assembler.add(0, b"AX", **options)


@pytest.mark.parametrize(
    "options",
    [
        {"generation": 2, "source_id": 0x27, "transaction_token": "read-1"},
        {"generation": 3, "source_id": 0x28, "transaction_token": "read-1"},
        {"generation": 3, "source_id": 0x27, "transaction_token": "read-0"},
    ],
)
def test_stale_generation_device_or_transaction_cannot_contaminate(
    options: dict[str, object],
) -> None:
    assembler = make_assembler(FragmentMarkerMode.INDEX)

    with pytest.raises(ProtocolError):
        assembler.add(0, b"AB", **options)  # ty: ignore[invalid-argument-type]

    assert assembler.missing_ranges() == ((0, 8),)


def test_missing_fragment_and_timeout_abort_remain_terminal() -> None:
    assembler = make_assembler(FragmentMarkerMode.BYTE_OFFSET)
    assembler.add(
        0,
        b"AB",
        generation=3,
        source_id=0x27,
        transaction_token="read-1",
    )

    assert assembler.missing_ranges() == ((2, 8),)
    with pytest.raises(ProtocolError, match="incomplete"):
        assembler.result()
    assembler.abort()
    with pytest.raises(ProtocolError, match="no longer active"):
        assembler.add(
            2,
            b"CD",
            generation=3,
            source_id=0x27,
            transaction_token="read-1",
        )
