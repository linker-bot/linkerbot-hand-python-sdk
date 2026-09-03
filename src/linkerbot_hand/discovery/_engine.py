"""Identity-only discovery orchestration across classic CAN and CAN FD."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

import can

from linkerbot_transport import CANRouteKey, SendOutcome
from linkerbot_transport import TimeoutError as RxTimeout
from linkerbot_transport.canfd import CANFDMessage

from ..api.enums import HandSide, TransportKind
from ..api.errors import HandError, IdentityMismatchError, ProtocolError
from ..capabilities.registry import get_declared_capabilities
from ..protocols.base import IdentityResult, ProtocolTransaction
from ..protocols.common.classic_can import (
    ClassicIdentityCollector,
    build_classic_message,
    classic_route,
    matching_data,
)
from ..protocols.l30.codec import PARENT_CONFIG, SUB_DEVICE_INFO, SUB_PRODUCT_CODE
from ..protocols.l30.framing import build_request as build_l30_request
from ..protocols.l30.framing import response_route as l30_response_route
from ..protocols.l30.identity import l30_identity_transaction
from ..protocols.o20.codec import (
    REG_DEVICE_INFO,
    device_id_for_side,
)
from ..protocols.o20.codec import (
    build_read_message as build_o20_read,
)
from ..protocols.o20.codec import (
    read_route as o20_read_route,
)
from ..protocols.o20.identity import o20_identity_transaction
from ..protocols.o30.codec import MI_PRODUCT_INFO
from ..protocols.o30.framing import (
    build_read_message as build_o30_read,
)
from ..protocols.o30.framing import (
    matching_response_frame,
)
from ..protocols.o30.framing import (
    response_route as o30_response_route,
)
from ..protocols.o30.identity import o30_identity_transaction
from ..protocols.registry import create_protocol_backend
from .contracts import (
    DiscoveryAddress,
    DiscoveryCandidate,
    DiscoveryChannel,
    DiscoveryConfig,
    DiscoveryFailure,
    DiscoveryIdentity,
    DiscoveryProblem,
    DiscoveryReport,
    DiscoverySource,
    DiscoveryStatus,
    TransportSpec,
)
from .errors import DiscoveryError
from .socketcan import SocketCANDiscoverySource

_CLASSIC_MODELS = {
    "LHL6": "L6",
    "LHO6": "O6",
    "LHT10": "L20Lite",
    # LHT20 is the current factory prefix. L25/LHL25 and LHG20 occur on
    # deployed devices; every wire identifier maps to the public L20 profile.
    "L20": "L20",
    "LHT20": "L20",
    "L25": "L20",
    "LHL25": "L20",
    "LHG20": "L20",
}


@dataclass(frozen=True, slots=True)
class _ClassicProbeOutcome:
    responded: bool
    failed: bool


class _NoResponse(Exception):
    def __init__(self, *, matched: bool) -> None:
        super().__init__("identity response timed out")
        self.matched = matched


class _ResponseError(Exception):
    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


class _TransportProbeError(Exception):
    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def discover_hands(
    config: DiscoveryConfig | None = None,
    *,
    source: DiscoverySource | None = None,
) -> DiscoveryReport:
    """Actively read identity from all selected channels and then close them."""

    resolved_config = DiscoveryConfig() if config is None else config
    if not isinstance(resolved_config, DiscoveryConfig):
        raise DiscoveryError(
            "config must be DiscoveryConfig", operation="discover_hands"
        )
    resolved_source = SocketCANDiscoverySource() if source is None else source
    if not isinstance(resolved_source, DiscoverySource):
        raise DiscoveryError(
            "source must implement DiscoverySource",
            operation="discover_hands",
        )
    started = time.monotonic()
    try:
        channels = resolved_source.list_channels(resolved_config)
    except DiscoveryError:
        raise
    except Exception as error:
        raise DiscoveryError(
            "discovery source could not list channels",
            operation="discover_hands",
            cause=error,
        ) from error
    if not isinstance(channels, tuple) or any(
        not isinstance(channel, DiscoveryChannel) for channel in channels
    ):
        raise DiscoveryError(
            "discovery source returned invalid channels",
            operation="discover_hands",
        )
    candidates: list[DiscoveryCandidate] = []
    failures: list[DiscoveryFailure] = []
    for channel in channels:
        classic = _ClassicProbeOutcome(responded=False, failed=False)
        if _channel_matches_classic(channel):
            classic = _probe_classic(
                resolved_source,
                channel,
                resolved_config,
                candidates,
                failures,
            )
        if (
            channel.kind is TransportKind.CAN_FD
            and not classic.responded
            and not classic.failed
        ):
            _probe_can_fd(
                resolved_source,
                channel,
                resolved_config,
                candidates,
                failures,
            )
    return DiscoveryReport(
        channels=channels,
        candidates=tuple(candidates),
        failures=tuple(failures),
        elapsed_s=max(0.0, time.monotonic() - started),
    )


def _probe_classic(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
    failures: list[DiscoveryFailure],
) -> _ClassicProbeOutcome:
    transport: object | None = None
    failed = False
    try:
        transport = source.create_transport(channel, TransportKind.CAN)
        _open_transport(transport)
        endpoints = (
            (HandSide.LEFT, classic_route(HandSide.LEFT)),
            (HandSide.RIGHT, classic_route(HandSide.RIGHT)),
        )
        messages = _collect(
            transport,
            tuple(route for _, route in endpoints),
            tuple(build_classic_message(route, b"\xc0") for _, route in endpoints),
            config,
        )
        grouped = _group_by_route(messages)
        responded = False
        for side, route in endpoints:
            responses = tuple(
                message
                for message in grouped.get(route, ())
                if (data := matching_data(message, route)) is not None
                and data
                and data[0] == 0xC0
            )
            if not responses:
                continue
            responded = True
            _resolve_classic_endpoint(
                source,
                channel,
                transport,
                side,
                route,
                responses,
                config,
                candidates,
                failures,
            )
    except Exception as error:  # noqa: BLE001 - discovery source boundary
        failed = True
        _record_failure(
            failures,
            operation="probe_classic_identity",
            error=error,
            channel=channel,
            wire_protocol=TransportKind.CAN,
        )
        responded = False
    finally:
        if transport is not None:
            try:
                _close_transport(transport)
            except Exception as error:  # noqa: BLE001 - transport boundary
                failed = True
                _record_failure(
                    failures,
                    operation="close_classic_probe_transport",
                    error=error,
                    channel=channel,
                    wire_protocol=TransportKind.CAN,
                )
    return _ClassicProbeOutcome(responded=responded, failed=failed)


def _resolve_classic_endpoint(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    transport: object,
    side: HandSide,
    route: CANRouteKey,
    responses: tuple[object, ...],
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
    failures: list[DiscoveryFailure],
) -> None:
    address = DiscoveryAddress(
        scheme="classic_can_id",
        request_id=route.arbitration_id,
        response_id=route.arbitration_id,
        is_extended_id=False,
    )
    spec = _transport_spec(source, channel, TransportKind.CAN)
    collector = ClassicIdentityCollector(route, l6_pcb_format=True)
    markers = tuple(
        data[1]
        for message in responses
        if (data := matching_data(message, route)) is not None and len(data) >= 2
    )
    duplicate_marker = len(markers) != len(set(markers))
    try:
        for message in responses:
            collector.offer(message)
        raw = collector.product_code_bytes
        product_code = collector.product_code()
    except HandError as error:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=None,
                side=side,
                raw_identity=_classic_response_bytes(responses, route),
                problem=(
                    DiscoveryProblem(
                        "address_collision",
                        "multiple classic identity fragments used the same marker",
                    )
                    if duplicate_marker
                    else _problem_from_error(error)
                ),
            )
        )
        return
    model = _classic_model(product_code)
    if duplicate_marker:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=model,
                side=side,
                product_code=product_code,
                raw_identity=raw,
                problem=DiscoveryProblem(
                    "address_collision",
                    "multiple classic identity fragments used the same marker",
                ),
            )
        )
        return
    if model is None:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=None,
                side=side,
                product_code=product_code,
                raw_identity=raw,
                problem=DiscoveryProblem(
                    "unknown_model",
                    f"unrecognized classic product code {product_code!r}",
                ),
            )
        )
        return
    capabilities = get_declared_capabilities(model, side=side)
    backend = create_protocol_backend(capabilities)
    try:
        result = _execute_identity(
            transport,
            cast(tuple[CANRouteKey, ...], backend.route_keys),
            backend.identity_transaction(capabilities),
            config,
        )
    except _NoResponse:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=model,
                side=side,
                product_code=product_code,
                raw_identity=raw,
                problem=DiscoveryProblem(
                    "identity_timeout",
                    "classic product code responded but complete identity timed out",
                ),
            )
        )
    except _ResponseError as wrapped:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=model,
                side=side,
                product_code=product_code,
                raw_identity=raw,
                problem=_problem_from_error(wrapped.error),
            )
        )
    except _TransportProbeError as wrapped:
        _record_failure(
            failures,
            operation="verify_classic_identity",
            error=wrapped.error,
            channel=channel,
            wire_protocol=TransportKind.CAN,
        )
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN,
                address=address,
                transport=spec,
                model=model,
                side=side,
                product_code=product_code,
                raw_identity=raw,
                problem=DiscoveryProblem(
                    "transport_error",
                    "transport failed during complete identity verification",
                ),
            )
        )
    else:
        candidates.append(
            _verified_candidate(
                channel,
                TransportKind.CAN,
                address,
                spec,
                result,
            )
        )


def _probe_can_fd(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
    failures: list[DiscoveryFailure],
) -> None:
    compatible_o20 = _channel_matches_model(channel, "O20", HandSide.LEFT)
    compatible_l30 = _channel_matches_model(channel, "L30", None)
    compatible_o30 = _channel_matches_model(channel, "O30", HandSide.LEFT)
    if not any((compatible_o20, compatible_l30, compatible_o30)):
        return
    transport: object | None = None
    try:
        transport = source.create_transport(channel, TransportKind.CAN_FD)
        _open_transport(transport)
        if compatible_o20:
            _probe_o20(source, channel, transport, config, candidates)
        if compatible_l30:
            _probe_l30(source, channel, transport, config, candidates, failures)
        if compatible_o30:
            _probe_o30(source, channel, transport, config, candidates, failures)
    except Exception as error:  # noqa: BLE001 - discovery source boundary
        _record_failure(
            failures,
            operation="probe_can_fd_identity",
            error=error,
            channel=channel,
            wire_protocol=TransportKind.CAN_FD,
        )
    finally:
        if transport is not None:
            try:
                _close_transport(transport)
            except Exception as error:  # noqa: BLE001 - transport boundary
                _record_failure(
                    failures,
                    operation="close_can_fd_probe_transport",
                    error=error,
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                )


def _probe_o20(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    transport: object,
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
) -> None:
    endpoints = tuple(
        (
            side,
            device_id_for_side(side),
            o20_read_route(device_id_for_side(side), REG_DEVICE_INFO),
        )
        for side in (HandSide.LEFT, HandSide.RIGHT)
    )
    messages = _collect(
        transport,
        tuple(route for _, _, route in endpoints),
        tuple(
            build_o20_read(device_id, REG_DEVICE_INFO) for _, device_id, _ in endpoints
        ),
        config,
    )
    grouped = _group_by_route(messages)
    spec = _transport_spec(source, channel, TransportKind.CAN_FD)
    for side, device_id, route in endpoints:
        responses = grouped.get(route, ())
        if not responses:
            continue
        address = DiscoveryAddress(
            scheme="o20_device_id",
            request_id=route.arbitration_id,
            response_id=route.arbitration_id,
            is_extended_id=True,
            node_id=device_id,
        )
        if len(responses) != 1:
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="O20",
                    side=side,
                    problem=DiscoveryProblem(
                        "address_collision",
                        "multiple O20 DeviceInfo responses used the same device ID",
                    ),
                )
            )
            continue
        capabilities = get_declared_capabilities("O20", side=side)
        transaction = o20_identity_transaction(capabilities)
        try:
            progress = transaction.consume(responses[0])
            result = _require_identity_progress(progress, transaction)
        except HandError as error:
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="O20",
                    side=side,
                    problem=_problem_from_error(error),
                )
            )
        else:
            candidates.append(
                _verified_candidate(
                    channel,
                    TransportKind.CAN_FD,
                    address,
                    spec,
                    result,
                )
            )


def _probe_l30(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    transport: object,
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
    failures: list[DiscoveryFailure],
) -> None:
    endpoints = tuple(
        (
            node_id,
            l30_response_route(
                parent=PARENT_CONFIG,
                subcommand=SUB_DEVICE_INFO,
                write=False,
                node_id=node_id,
            ),
        )
        for node_id in config.l30_node_ids
    )
    requests = tuple(
        build_l30_request(
            parent=PARENT_CONFIG,
            subcommand=SUB_DEVICE_INFO,
            write=False,
            node_id=node_id,
        )
        for node_id, _ in endpoints
    )
    messages = _collect(
        transport,
        tuple(route for _, route in endpoints),
        requests,
        config,
    )
    grouped = _group_by_route(messages)
    spec = _transport_spec(source, channel, TransportKind.CAN_FD)
    for node_id, route in endpoints:
        responses = grouped.get(route, ())
        if not responses:
            continue
        request = build_l30_request(
            parent=PARENT_CONFIG,
            subcommand=SUB_DEVICE_INFO,
            write=False,
            node_id=node_id,
        )
        address = DiscoveryAddress(
            scheme="l30_node_id",
            request_id=request.arbitration_id,
            response_id=route.arbitration_id,
            is_extended_id=True,
            node_id=node_id,
        )
        if len(responses) != 1:
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="L30",
                    side=HandSide.NOT_APPLICABLE,
                    problem=DiscoveryProblem(
                        "address_collision",
                        "multiple L30 DeviceInfo responses used the same node ID",
                    ),
                )
            )
            continue
        capabilities = get_declared_capabilities("L30")
        transaction = l30_identity_transaction(
            capabilities,
            node_id=node_id,
            host_id=0,
        )
        try:
            progress = transaction.consume(responses[0])
            if not progress.matched or not progress.complete:
                raise ProtocolError("L30 DeviceInfo probe response was incomplete")
            next_transaction = transaction.build_next_transaction()
            if next_transaction is None:
                raise ProtocolError("L30 identity omitted its product-code stage")
            routes = (
                route,
                l30_response_route(
                    parent=PARENT_CONFIG,
                    subcommand=SUB_PRODUCT_CODE,
                    write=False,
                    node_id=node_id,
                ),
            )
            result = _execute_identity(
                transport,
                routes,
                next_transaction,
                config,
            )
        except _NoResponse:
            problem = DiscoveryProblem(
                "identity_timeout",
                "L30 DeviceInfo responded but product code timed out",
            )
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="L30",
                    side=HandSide.NOT_APPLICABLE,
                    problem=problem,
                )
            )
        except _ResponseError as wrapped:
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="L30",
                    side=HandSide.NOT_APPLICABLE,
                    problem=_problem_from_error(wrapped.error),
                )
            )
        except _TransportProbeError as wrapped:
            _record_failure(
                failures,
                operation="verify_l30_identity",
                error=wrapped.error,
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
            )
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="L30",
                    side=HandSide.NOT_APPLICABLE,
                    problem=DiscoveryProblem(
                        "transport_error",
                        "transport failed during complete L30 identity verification",
                    ),
                )
            )
        except HandError as error:
            candidates.append(
                _partial_candidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    transport=spec,
                    model="L30",
                    side=HandSide.NOT_APPLICABLE,
                    problem=_problem_from_error(error),
                )
            )
        else:
            candidates.append(
                _verified_candidate(
                    channel,
                    TransportKind.CAN_FD,
                    address,
                    spec,
                    result,
                    reported_side=_l30_reported_side(result),
                )
            )


def _probe_o30(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    transport: object,
    config: DiscoveryConfig,
    candidates: list[DiscoveryCandidate],
    failures: list[DiscoveryFailure],
) -> None:
    route = o30_response_route()
    messages = _collect(
        transport,
        (route,),
        (build_o30_read(MI_PRODUCT_INFO, sub_index=0, length=0x10),),
        config,
    )
    matching: list[object] = []
    malformed: Exception | None = None
    for message in messages:
        try:
            frame = matching_response_frame(message)
        except HandError as error:
            malformed = error
            continue
        if (
            frame is not None
            and frame.main_index == MI_PRODUCT_INFO
            and frame.sub_index == 0
            and not frame.rts
        ):
            matching.append(message)
    if not matching and malformed is None:
        return
    spec = _transport_spec(source, channel, TransportKind.CAN_FD)
    address = DiscoveryAddress(
        scheme="o30_hop_endpoint",
        request_id=0x001,
        response_id=route.arbitration_id,
        is_extended_id=False,
    )
    if malformed is not None and not matching:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=_problem_from_error(malformed),
            )
        )
        return
    if len(matching) != 1:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=DiscoveryProblem(
                    "address_collision",
                    "multiple O30 product responses used the fixed HOP endpoint",
                ),
            )
        )
        return
    provisional = get_declared_capabilities("O30", side=HandSide.LEFT)
    transaction = o30_identity_transaction(provisional)
    try:
        progress = transaction.consume(matching[0])
        if not progress.matched or not progress.complete:
            raise ProtocolError("O30 product-model probe response was incomplete")
        next_transaction = transaction.build_next_transaction()
        if next_transaction is None:
            raise ProtocolError("O30 identity omitted required fields")
        result = _execute_identity(
            transport,
            (route,),
            next_transaction,
            config,
        )
    except _NoResponse:
        problem = DiscoveryProblem(
            "identity_timeout",
            "O30 product model responded but complete identity timed out",
        )
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=problem,
            )
        )
    except _ResponseError as wrapped:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=_problem_from_error(wrapped.error),
            )
        )
    except _TransportProbeError as wrapped:
        _record_failure(
            failures,
            operation="verify_o30_identity",
            error=wrapped.error,
            channel=channel,
            wire_protocol=TransportKind.CAN_FD,
        )
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=DiscoveryProblem(
                    "transport_error",
                    "transport failed during complete O30 identity verification",
                ),
            )
        )
    except HandError as error:
        candidates.append(
            _partial_candidate(
                channel=channel,
                wire_protocol=TransportKind.CAN_FD,
                address=address,
                transport=spec,
                model="O30",
                side=None,
                problem=_problem_from_error(error),
            )
        )
    else:
        reported_side = result.device_info.reported_side
        if reported_side is not None:
            candidates.append(
                _verified_candidate(
                    channel,
                    TransportKind.CAN_FD,
                    address,
                    spec,
                    result,
                    reported_side=reported_side,
                )
            )
        else:
            candidates.append(
                DiscoveryCandidate(
                    channel=channel,
                    wire_protocol=TransportKind.CAN_FD,
                    address=address,
                    status=DiscoveryStatus.PARTIAL,
                    identity=_identity_from_result(result, side=None),
                    transport=spec,
                    problems=(
                        DiscoveryProblem(
                            "missing_side",
                            "O30 HOP identity does not provide a verified hand side",
                        ),
                    ),
                )
            )


def _collect(
    transport: object,
    route_keys: tuple[CANRouteKey, ...],
    requests: tuple[object, ...],
    config: DiscoveryConfig,
) -> tuple[object, ...]:
    subscribe = getattr(transport, "subscribe_routes", None)
    if not callable(subscribe):
        raise DiscoveryError("probe transport lacks subscribe_routes()")
    subscription = subscribe(
        route_keys,
        queue_capacity=config.route_queue_capacity,
    )
    messages: list[object] = []
    try:
        _send_requests(transport, requests, config)
        deadline = time.monotonic() + config.response_timeout_s
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0:
                break
            try:
                routed = subscription.receive(timeout_s=remaining)
            except RxTimeout:
                break
            messages.append(routed.message)
    finally:
        subscription.close()
    return tuple(messages)


def _execute_identity(
    transport: object,
    route_keys: tuple[CANRouteKey, ...],
    transaction: ProtocolTransaction,
    config: DiscoveryConfig,
) -> IdentityResult:
    subscribe = getattr(transport, "subscribe_routes", None)
    if not callable(subscribe):
        raise _TransportProbeError(
            DiscoveryError("probe transport lacks subscribe_routes()")
        )
    try:
        subscription = subscribe(
            route_keys,
            queue_capacity=config.route_queue_capacity,
        )
    except Exception as error:
        raise _TransportProbeError(error) from error
    matched_any = False
    current: ProtocolTransaction | None = transaction
    result: IdentityResult | None = None
    try:
        while current is not None:
            try:
                _send_requests(
                    transport,
                    current.request_messages,
                    config,
                    minimum_interval_s=current.request_interval_s,
                )
            except Exception as error:
                raise _TransportProbeError(error) from error
            deadline = time.monotonic() + config.response_timeout_s
            complete = False
            while not complete:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    raise _NoResponse(matched=matched_any)
                try:
                    routed = subscription.receive(timeout_s=remaining)
                except RxTimeout as error:
                    raise _NoResponse(matched=matched_any) from error
                try:
                    progress = current.consume(routed.message)
                except Exception as error:
                    raise _ResponseError(error) from error
                if not progress.matched:
                    continue
                matched_any = True
                complete = progress.complete
                if isinstance(progress.result, IdentityResult):
                    result = progress.result
            try:
                current = current.build_next_transaction()
            except Exception as error:
                raise _ResponseError(error) from error
    finally:
        subscription.close()
    if result is None:
        raise _ResponseError(ProtocolError("identity transaction returned no result"))
    return result


def _send_requests(
    transport: object,
    requests: tuple[object, ...],
    config: DiscoveryConfig,
    *,
    minimum_interval_s: float = 0.0,
) -> None:
    send = getattr(transport, "send", None)
    if not callable(send):
        raise DiscoveryError("probe transport lacks send()")
    for index, request in enumerate(requests):
        receipt = send(request)
        result = receipt.result(timeout=config.send_timeout_s)
        if result.outcome is not SendOutcome.DISPATCHED:
            raise DiscoveryError(
                f"probe send completed as {result.outcome.value}",
                operation="send_identity_probe",
            )
        interval = max(config.inter_request_delay_s, minimum_interval_s)
        if index + 1 < len(requests) and interval:
            time.sleep(interval)


def _open_transport(transport: object) -> None:
    open_transport = getattr(transport, "open", None)
    if not callable(open_transport):
        raise DiscoveryError("probe transport lacks open()")
    open_transport()


def _close_transport(transport: object) -> None:
    close_transport = getattr(transport, "close", None)
    if not callable(close_transport):
        raise DiscoveryError("probe transport lacks close()")
    close_transport()


def _group_by_route(
    messages: tuple[object, ...],
) -> dict[CANRouteKey, tuple[object, ...]]:
    grouped: defaultdict[CANRouteKey, list[object]] = defaultdict(list)
    for message in messages:
        if not isinstance(message, (can.Message, CANFDMessage)):
            continue
        grouped[CANRouteKey(message.arbitration_id, message.is_extended_id)].append(
            message
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _classic_response_bytes(
    responses: tuple[object, ...],
    route: CANRouteKey,
) -> bytes | None:
    fragments: list[bytes] = []
    for response in responses:
        data = matching_data(response, route)
        if data is not None and len(data) >= 3:
            fragments.append(data[2:])
    raw = b"".join(fragments).rstrip(b"\x00")
    return raw or None


def _classic_model(product_code: str) -> str | None:
    prefix = product_code.split("-", 1)[0].strip().upper()
    return _CLASSIC_MODELS.get(prefix)


def _channel_matches_model(
    channel: DiscoveryChannel,
    model: str,
    side: HandSide | None,
) -> bool:
    capabilities = get_declared_capabilities(model, side=side)
    required = capabilities.transport
    return (
        channel.nominal_bitrate is None
        or channel.nominal_bitrate == required.nominal_bitrate
    ) and (
        channel.data_bitrate is None or channel.data_bitrate == required.data_bitrate
    )


def _channel_matches_classic(channel: DiscoveryChannel) -> bool:
    required = get_declared_capabilities("L6", side=HandSide.LEFT).transport
    return (
        channel.nominal_bitrate is None
        or channel.nominal_bitrate == required.nominal_bitrate
    )


def _transport_spec(
    source: DiscoverySource,
    channel: DiscoveryChannel,
    wire_protocol: TransportKind,
) -> TransportSpec:
    return TransportSpec(
        provider=channel.provider,
        channel=channel.name,
        kind=wire_protocol,
        nominal_bitrate=channel.nominal_bitrate,
        data_bitrate=(
            channel.data_bitrate if wire_protocol is TransportKind.CAN_FD else None
        ),
        factory=lambda: source.create_transport(channel, wire_protocol),
    )


def _verified_candidate(
    channel: DiscoveryChannel,
    wire_protocol: TransportKind,
    address: DiscoveryAddress,
    transport: TransportSpec,
    result: IdentityResult,
    *,
    reported_side: HandSide | None = None,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        channel=channel,
        wire_protocol=wire_protocol,
        address=address,
        status=DiscoveryStatus.VERIFIED,
        identity=_identity_from_result(result, side=result.device_info.side),
        transport=transport,
        reported_side=reported_side,
        capabilities=result.capabilities,
        device_info=result.device_info,
    )


def _identity_from_result(
    result: IdentityResult,
    *,
    side: HandSide | None,
) -> DiscoveryIdentity:
    info = result.device_info
    return DiscoveryIdentity(
        model=info.model,
        side=side,
        serial_number=info.serial_number,
        pcb_version=info.pcb_version,
        firmware_version=info.firmware_version,
        mechanical_version=info.mechanical_version,
        product_code=info.product_code,
        sensor_variant=info.sensor_variant,
        raw_identity=info.raw_identity,
        fingerprint=result.fingerprint,
    )


def _partial_candidate(
    *,
    channel: DiscoveryChannel,
    wire_protocol: TransportKind,
    address: DiscoveryAddress,
    transport: TransportSpec,
    model: str | None,
    side: HandSide | None,
    problem: DiscoveryProblem,
    product_code: str | None = None,
    raw_identity: bytes | tuple[int, ...] | None = None,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        channel=channel,
        wire_protocol=wire_protocol,
        address=address,
        status=DiscoveryStatus.PARTIAL,
        identity=DiscoveryIdentity(
            model=model,
            side=side,
            product_code=product_code,
            raw_identity=raw_identity,
        ),
        transport=transport,
        problems=(problem,),
    )


def _problem_from_error(error: Exception) -> DiscoveryProblem:
    if isinstance(error, IdentityMismatchError):
        code = "identity_mismatch"
    elif isinstance(error, ProtocolError):
        code = "malformed_identity"
    elif isinstance(error, HandError):
        code = error.code.value
    else:
        code = "malformed_identity"
    message = str(error) or type(error).__name__
    return DiscoveryProblem(code, message)


def _record_failure(
    failures: list[DiscoveryFailure],
    *,
    operation: str,
    error: Exception,
    channel: DiscoveryChannel,
    wire_protocol: TransportKind,
) -> None:
    cause = error.error if isinstance(error, _TransportProbeError) else error
    code_value = getattr(getattr(cause, "code", None), "value", None)
    failures.append(
        DiscoveryFailure(
            operation=operation,
            code=code_value or "discovery_failure",
            message=str(cause) or type(cause).__name__,
            error_type=type(cause).__name__,
            channel=channel,
            wire_protocol=wire_protocol,
        )
    )


def _require_identity_progress(
    progress: object,
    transaction: ProtocolTransaction,
) -> IdentityResult:
    if not getattr(progress, "matched", False) or not getattr(
        progress, "complete", False
    ):
        raise ProtocolError("identity response was incomplete")
    result = getattr(progress, "result", None)
    if (
        transaction.next_transaction is not None
        or transaction.next_transaction_factory is not None
    ):
        raise ProtocolError("identity response unexpectedly requires another stage")
    if not isinstance(result, IdentityResult):
        raise ProtocolError("identity response returned no verified result")
    return result


def _l30_reported_side(result: IdentityResult) -> HandSide:
    product_code = result.device_info.product_code
    if product_code is None:
        raise ProtocolError("L30 verified identity has no product code")
    parts = product_code.split("-")
    if len(parts) < 4:
        raise ProtocolError("L30 product code does not contain a side")
    if parts[3] == "L":
        return HandSide.LEFT
    if parts[3] == "R":
        return HandSide.RIGHT
    raise ProtocolError("L30 product code contains an unknown side")


__all__ = ["discover_hands"]
