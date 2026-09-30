"""Pure, deterministic aliases for verified discovery candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api.enums import HandSide
from ..api.errors import ValidationError
from ..capabilities.registry import SUPPORTED_MODELS
from .errors import IncompleteIdentityError

if TYPE_CHECKING:
    from .contracts import DiscoveryCandidate

_EndpointKey = tuple[str, str, str, str, int, int, bool, int]


def name_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
) -> dict[str, DiscoveryCandidate]:
    """Validate the full selection before assigning any aliases or building I/O."""

    groups: dict[str, list[tuple[_EndpointKey, DiscoveryCandidate]]] = {}
    endpoints: set[_EndpointKey] = set()
    for candidate in candidates:
        base = _base_name(candidate)
        key = _endpoint_key(candidate)
        if key in endpoints:
            raise ValidationError(
                f"duplicate discovery endpoint on "
                f"{candidate.channel.provider}:{candidate.channel.name}: "
                f"{candidate.address}",
                operation="discovery_named_candidates",
                model=candidate.model,
            )
        endpoints.add(key)
        groups.setdefault(base, []).append((key, candidate))
    named: dict[str, DiscoveryCandidate] = {}
    for base, group in sorted(groups.items()):
        for index, (_, candidate) in enumerate(sorted(group, key=lambda v: v[0]), 1):
            name = base if len(group) == 1 else f"{base}{index}"
            named[name] = candidate
    return named


def _base_name(candidate: DiscoveryCandidate) -> str:
    model = candidate.model
    capabilities = candidate.capabilities
    info = candidate.device_info
    context = f"{candidate.channel.provider}:{candidate.channel.name}"
    if model is None or capabilities is None or info is None:
        raise IncompleteIdentityError(
            f"candidate on {context} has incomplete identity",
            operation="discovery_named_candidates",
            model=model,
        )
    if (
        model not in SUPPORTED_MODELS
        or (capabilities.model, capabilities.side) != (model, candidate.side)
        or (info.model, info.side) != (model, candidate.side)
        or (model == "L30" and candidate.side is not HandSide.NOT_APPLICABLE)
    ):
        raise ValidationError(
            f"candidate on {context} has inconsistent model or side",
            operation="discovery_named_candidates",
            model=model,
        )
    side = candidate.reported_side if model == "L30" else candidate.side
    if side not in {HandSide.LEFT, HandSide.RIGHT}:
        raise IncompleteIdentityError(
            f"candidate on {context} has no verified physical side",
            operation="discovery_named_candidates",
            model=model,
        )
    if any(
        reported is not None and reported is not side
        for reported in (candidate.reported_side, info.reported_side)
    ):
        raise ValidationError(
            f"candidate on {context} has conflicting reported sides",
            operation="discovery_named_candidates",
            model=model,
        )
    return f"{model.lower()}{'l' if side is HandSide.LEFT else 'r'}"


def _endpoint_key(candidate: DiscoveryCandidate) -> _EndpointKey:
    address = candidate.address
    return (
        candidate.channel.provider,
        candidate.channel.name,
        candidate.wire_protocol.value,
        address.scheme,
        address.request_id,
        address.response_id,
        address.is_extended_id,
        -1 if address.node_id is None else address.node_id,
    )
