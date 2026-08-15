from __future__ import annotations

import builtins
from enum import Flag
from types import SimpleNamespace

import pytest

from linkerbot.exceptions import StateError
from linkerbot.hand import device as device_module
from linkerbot.hand.o30i import protocol as o30i_protocol
from linkerbot_hand import (
    AckSupport,
    ClosedError,
    CommandKind,
    CommandStage,
    ConnectionState,
    DeviceIdentity,
    DeviceNotReadyError,
    ErrorCode,
    HandCommand,
    HandDevice,
    HandSide,
    SensorType,
    TactileState,
    TransportError,
    get_model_capabilities,
)
from tests.hand.o30i.fakes import FakeO30iDispatcher


class _Dispatcher:
    def __init__(self) -> None:
        self.queued: list[tuple[object, ...]] = []
        self.sent_now: list[tuple[object, ...]] = []
        self.send_now_error: BaseException | None = None
        self.send_now_error_at: int | None = None
        self.send_now_calls = 0

    def send(self, values) -> None:
        self.queued.append(tuple(values))

    def send_now(self, values) -> None:
        self.send_now_calls += 1
        if self.send_now_error is not None and (
            self.send_now_error_at is None
            or self.send_now_calls == self.send_now_error_at
        ):
            raise self.send_now_error
        self.sent_now.append(tuple(values))


class _ReadingManager:
    def __init__(self) -> None:
        self.data = None

    def get_blocking(self):
        return self.data


class _AngleManager:
    def __init__(self, dispatcher, *, timeout_after_send: bool = False) -> None:
        self._dispatcher = dispatcher
        self._timeout_after_send = timeout_after_send
        self.data = None
        self.raw_submissions: list[tuple[int | float, ...]] = []
        self.hardware_raw_submissions: list[tuple[int | float, ...]] = []

    def get_blocking(self):
        return self.data

    def set_raw_angles(self, values) -> None:
        submitted = tuple(values)
        self.raw_submissions.append(submitted)
        self._dispatcher.send(submitted)
        if self._timeout_after_send:
            raise builtins.TimeoutError("injected device ACK timeout")

    def set_hardware_raw_angles(self, values) -> None:
        submitted = tuple(values)
        self.hardware_raw_submissions.append(submitted)
        self._dispatcher.send(submitted)
        if self._timeout_after_send:
            raise builtins.TimeoutError("injected device ACK timeout")


class _LegacyL6:
    instances: list[_LegacyL6] = []

    def __init__(self, **options) -> None:
        self.options = options
        self._dispatcher = options["dispatcher"]
        self.angle = _AngleManager(self._dispatcher)
        self.force_sensor = _ReadingManager()
        self.fault = _ReadingManager()
        self.version = SimpleNamespace(
            get_device_info=lambda: SimpleNamespace(
                firmware_version="1.2.3",
                serial_number="TEST-L6",
            )
        )
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _LegacyO30i:
    instances: list[_LegacyO30i] = []

    def __init__(self, *, product_model: str = "O30i", **options) -> None:
        self.options = options
        self._dispatcher = options["dispatcher"]
        self.angle = _AngleManager(self._dispatcher, timeout_after_send=True)
        self.version = SimpleNamespace(
            get_device_info=lambda: SimpleNamespace(
                product_model=product_model,
                application_version="4.5.6",
                device_uid="TEST-O30I",
            )
        )
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _LegacyO20:
    instances: list[_LegacyO20] = []

    def __init__(self, *, product_model: str = "O20-R", **options) -> None:
        self.options = options
        self.version = SimpleNamespace(
            get_device_info=lambda: SimpleNamespace(
                product_model=product_model,
                software_version="2.3.4",
                hand_side=SimpleNamespace(value="right"),
                serial_number="TEST-O20",
            )
        )
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _LegacyL30:
    instances: list[_LegacyL30] = []

    def __init__(self, *, product_id: int, hand_side: str = "right", **options) -> None:
        self.options = options
        dispatcher = options.get("dispatcher")
        if dispatcher is not None:
            self._dispatcher = dispatcher
            self.angle = _AngleManager(dispatcher)
        self.version = SimpleNamespace(
            get_device_info=lambda: SimpleNamespace(
                product_id=product_id,
                software_version="7.8.9",
                hand_side=SimpleNamespace(value=hand_side),
                serial_number=12345,
            )
        )
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _FaultCode(Flag):
    NONE = 0
    OVERCURRENT = 1
    OVERTEMPERATURE = 4


class _FaultVector:
    def __init__(self, values: tuple[_FaultCode, ...]) -> None:
        self._values = values

    def to_list(self) -> list[_FaultCode]:
        return list(self._values)


def _install_model(monkeypatch, model: str, model_class) -> None:
    monkeypatch.setattr(
        device_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(**{model: model_class}),
    )


def _open_l6_backend(
    monkeypatch,
) -> tuple[device_module.LegacyHandBackend, _Dispatcher, _LegacyL6]:
    _install_model(monkeypatch, "L6", _LegacyL6)
    dispatcher = _Dispatcher()
    backend = device_module.LegacyHandBackend(
        "L6",
        side=HandSide.RIGHT,
        options={"dispatcher": dispatcher},
    )
    backend.open()
    return backend, dispatcher, _LegacyL6.instances[-1]


def test_legacy_backend_retains_hand_when_close_fails_for_retry(monkeypatch) -> None:
    backend, _dispatcher, hand = _open_l6_backend(monkeypatch)
    original_close = hand.close
    close_attempts = 0

    def flaky_close() -> None:
        nonlocal close_attempts
        close_attempts += 1
        if close_attempts == 1:
            raise OSError("injected close failure")
        original_close()

    monkeypatch.setattr(hand, "close", flaky_close)

    with pytest.raises(TransportError, match="injected close failure"):
        backend.close()

    assert backend._hand is hand
    assert not hand.closed

    backend.close()

    assert close_attempts == 2
    assert hand.closed
    assert backend._hand is None


def test_legacy_runtime_state_error_maps_to_closed_error(monkeypatch) -> None:
    backend, _dispatcher, hand = _open_l6_backend(monkeypatch)

    def raise_closed_state() -> None:
        raise StateError("legacy client is closed")

    monkeypatch.setattr(hand.angle, "get_blocking", raise_closed_state)
    try:
        with pytest.raises(ClosedError, match="legacy client is closed") as exc_info:
            backend.poll(SensorType.ANGLES)

        assert isinstance(exc_info.value.cause, StateError)
    finally:
        backend.close()


def test_legacy_dispatch_is_immediate_without_replacing_shared_send(
    monkeypatch,
) -> None:
    backend, dispatcher, hand = _open_l6_backend(monkeypatch)
    original_send = dispatcher.send.__func__
    marked = []
    percentages = (0, 1, 25, 50, 75, 100)
    expected_raw = (0, 3, 64, 128, 191, 255)
    try:
        result = backend.dispatch(
            HandCommand(CommandKind.ANGLES, percentages),
            claim_dispatch=lambda: True,
            mark_dispatched=lambda: marked.append(True),
        )

        assert result.acknowledged is None
        assert hand.angle.hardware_raw_submissions == [expected_raw]
        assert hand.angle.raw_submissions == []
        assert dispatcher.sent_now == [expected_raw]
        assert dispatcher.queued == []
        assert marked == [True]
        assert dispatcher.send.__func__ is original_send
        dispatcher.send(("background",))
        assert dispatcher.queued == [("background",)]
        assert hand.options["auto_start_polling"] is False
    finally:
        backend.close()


def test_unified_raw_submission_uses_protocol_values_without_legacy_mapping(
    monkeypatch,
) -> None:
    backend, dispatcher, hand = _open_l6_backend(monkeypatch)
    raw = (1, 2, 3, 4, 5, 6)
    try:
        backend.dispatch(
            HandCommand(CommandKind.RAW_ANGLES, raw),
            claim_dispatch=lambda: True,
            mark_dispatched=lambda: None,
        )

        assert hand.angle.hardware_raw_submissions == [raw]
        assert hand.angle.raw_submissions == []
        assert dispatcher.sent_now == [raw]
    finally:
        backend.close()


def test_unified_angle_poll_preserves_protocol_raw_values(monkeypatch) -> None:
    backend, _dispatcher, hand = _open_l6_backend(monkeypatch)
    hand.angle.data = SimpleNamespace(
        angles=(100.0, 50.0, 0.0, 25.0, 75.0, 10.0),
        raw_angles=(255, 123, 0, 64, 191, 26),
    )
    try:
        reading = backend.poll(SensorType.ANGLES)

        assert reading.raw_values == (255, 123, 0, 64, 191, 26)
        assert reading.values == pytest.approx(
            (
                100.0,
                123 * 100 / 255,
                0.0,
                64 * 100 / 255,
                191 * 100 / 255,
                26 * 100 / 255,
            )
        )
    finally:
        backend.close()


def test_real_o30i_motion_time_poll_extracts_ticks() -> None:
    product_info = bytearray(o30i_protocol.O30I_PRODUCT_INFO_BYTE_LENGTH)
    product_info[0:4] = b"O30i"
    product_info[0x39:0x3E] = b"UID-1"
    product_info[0x89:0x8E] = b"1.2.3"
    runtime = bytes(range(o30i_protocol.O30I_RUNTIME_SLOT_COUNT))
    dispatcher = FakeO30iDispatcher(
        {
            o30i_protocol.O30I_MI_PRODUCT_INFO: bytes(product_info),
            o30i_protocol.O30I_MI_MOTION_TIME: runtime,
        }
    )
    backend = device_module.LegacyHandBackend(
        "O30i",
        side=HandSide.RIGHT,
        options={"dispatcher": dispatcher},
    )
    try:
        backend.open()

        reading = backend.poll(SensorType.MOTION_TIME)

        assert reading.sensor is SensorType.MOTION_TIME
        assert reading.values == tuple(
            runtime[index] for index in o30i_protocol.O30I_JOINT_SLOT_INDICES
        )
        assert dispatcher.sent[-1].data == bytes(
            (o30i_protocol.O30I_MI_MOTION_TIME, 0, len(runtime))
        )
    finally:
        backend.close()


def test_legacy_dispatch_does_not_mark_failed_immediate_send(monkeypatch) -> None:
    backend, dispatcher, _hand = _open_l6_backend(monkeypatch)
    original_send = dispatcher.send.__func__
    dispatcher.send_now_error = OSError("injected transport failure")
    marked = []
    try:
        with pytest.raises(TransportError, match="injected transport failure"):
            backend.dispatch(
                HandCommand(CommandKind.ANGLES, (10, 20, 30, 40, 50, 60)),
                claim_dispatch=lambda: True,
                mark_dispatched=lambda: marked.append(True),
            )

        assert marked == []
        assert dispatcher.sent_now == []
        assert dispatcher.queued == []
        assert dispatcher.send.__func__ is original_send
    finally:
        backend.close()


def test_legacy_dispatch_claim_rejection_suppresses_transport_send(monkeypatch) -> None:
    backend, dispatcher, _hand = _open_l6_backend(monkeypatch)
    claimed = []
    marked = []
    try:
        result = backend.dispatch(
            HandCommand(CommandKind.ANGLES, (10, 20, 30, 40, 50, 60)),
            claim_dispatch=lambda: claimed.append(True) or False,
            mark_dispatched=lambda: marked.append(True),
        )

        assert result.acknowledged is None
        assert claimed == [True]
        assert dispatcher.sent_now == []
        assert dispatcher.queued == []
        assert marked == []
    finally:
        backend.close()


def test_claim_rejection_aborts_ack_wait_without_failing_connection(
    monkeypatch,
) -> None:
    _install_model(monkeypatch, "O30i", _LegacyO30i)
    dispatcher = _Dispatcher()
    backend = device_module.LegacyHandBackend(
        "O30i",
        side=HandSide.RIGHT,
        options={"dispatcher": dispatcher},
    )
    backend.open()
    try:
        result = backend.dispatch(
            HandCommand(CommandKind.ANGLES, (50,) * 20),
            claim_dispatch=lambda: False,
            mark_dispatched=lambda: pytest.fail("must not mark dispatched"),
        )

        assert result.acknowledged is None
        assert dispatcher.send_now_calls == 0
        assert dispatcher.sent_now == []
    finally:
        backend.close()


def test_injected_dispatcher_without_send_now_is_rejected(monkeypatch) -> None:
    backend, dispatcher, _hand = _open_l6_backend(monkeypatch)
    monkeypatch.setattr(dispatcher, "send_now", None)
    claimed = []
    marked = []
    try:
        with pytest.raises(TransportError, match="must provide callable send_now"):
            backend.dispatch(
                HandCommand(CommandKind.ANGLES, (10, 20, 30, 40, 50, 60)),
                claim_dispatch=lambda: claimed.append(True) or True,
                mark_dispatched=lambda: marked.append(True),
            )

        assert claimed == []
        assert dispatcher.queued == []
        assert marked == []
    finally:
        backend.close()


def test_first_successful_frame_stays_dispatched_when_later_frame_fails(
    monkeypatch,
) -> None:
    backend, dispatcher, hand = _open_l6_backend(monkeypatch)
    dispatcher.send_now_error = OSError("injected second-frame failure")
    dispatcher.send_now_error_at = 2
    original_set_raw_angles = hand.angle.set_hardware_raw_angles

    def send_two_frames(values) -> None:
        original_set_raw_angles(values)
        original_set_raw_angles(values)

    monkeypatch.setattr(hand.angle, "set_hardware_raw_angles", send_two_frames)
    claimed = []
    marked = []
    try:
        with pytest.raises(TransportError, match="injected second-frame failure"):
            backend.dispatch(
                HandCommand(CommandKind.ANGLES, (10, 20, 30, 40, 50, 60)),
                claim_dispatch=lambda: claimed.append(True) or True,
                mark_dispatched=lambda: marked.append(True),
            )

        assert claimed == [True]
        assert len(dispatcher.sent_now) == 1
        assert marked == [True]
    finally:
        backend.close()


def test_successful_send_is_dispatched_even_when_ack_then_times_out(
    monkeypatch,
) -> None:
    _install_model(monkeypatch, "O30i", _LegacyO30i)
    dispatcher = _Dispatcher()
    capabilities = get_model_capabilities("O30i", side=HandSide.RIGHT)
    device = HandDevice(
        capabilities,
        lambda: device_module.LegacyHandBackend(
            "O30i",
            side=HandSide.RIGHT,
            options={"dispatcher": dispatcher},
        ),
    )
    device.open()
    try:
        handle = device.submit_angles((50,) * capabilities.joint_count)

        assert handle.wait(1.0) is CommandStage.FAILED
        assert handle.ack_support is AckSupport.SUPPORTED
        assert [transition.stage for transition in handle.history] == [
            CommandStage.ACCEPTED,
            CommandStage.DISPATCHED,
            CommandStage.FAILED,
        ]
        assert handle.error is not None
        assert handle.error.code is ErrorCode.TIMEOUT
        assert len(dispatcher.sent_now) == 1
        assert device.get_health().last_successful_send_at is not None
        assert device.get_health().connection_state is ConnectionState.ERROR
    finally:
        device.close()


def test_force_poll_normalizes_five_fingers_to_tactile_states(monkeypatch) -> None:
    backend, _dispatcher, hand = _open_l6_backend(monkeypatch)
    matrices = {
        finger: SimpleNamespace(
            values=((index, index + 1), (index + 2, index + 3)),
            online=index % 2 == 0,
        )
        for index, finger in enumerate(("thumb", "index", "middle", "ring", "pinky"))
    }
    hand.force_sensor.data = SimpleNamespace(**matrices)
    try:
        reading = backend.poll(SensorType.FORCE)

        assert reading.sensor is SensorType.FORCE
        assert reading.raw_values is None
        assert reading.values == tuple(
            TactileState(
                finger=finger,
                rows=2,
                columns=2,
                values=tuple(tuple(row) for row in matrices[finger].values),
                online=matrices[finger].online,
            )
            for finger in ("thumb", "index", "middle", "ring", "pinky")
        )
    finally:
        backend.close()


def test_fault_poll_normalizes_enum_members_to_plain_ints(monkeypatch) -> None:
    backend, _dispatcher, hand = _open_l6_backend(monkeypatch)
    fault_codes = (
        _FaultCode.NONE,
        _FaultCode.OVERCURRENT,
        _FaultCode.OVERTEMPERATURE,
        _FaultCode.OVERCURRENT | _FaultCode.OVERTEMPERATURE,
        _FaultCode.NONE,
        _FaultCode.OVERCURRENT,
    )
    hand.fault.data = SimpleNamespace(faults=_FaultVector(fault_codes))
    try:
        reading = backend.poll(SensorType.FAULTS)

        assert reading.values == (0, 1, 4, 5, 0, 1)
        assert all(type(value) is int for value in reading.values)
    finally:
        backend.close()


@pytest.mark.parametrize("product_id", [0x12, 0x14])
def test_l30_identity_rejects_wrong_product_id(monkeypatch, product_id: int) -> None:
    _install_model(
        monkeypatch,
        "L30",
        lambda **options: _LegacyL30(product_id=product_id, **options),
    )
    backend = device_module.LegacyHandBackend(
        "L30",
        side=HandSide.RIGHT,
        options={},
    )

    with pytest.raises(DeviceNotReadyError, match="expected L30 product id 0x13"):
        backend.open()
    assert _LegacyL30.instances[-1].closed is True


def test_l30_identity_accepts_protocol_product_id(monkeypatch) -> None:
    _install_model(
        monkeypatch,
        "L30",
        lambda **options: _LegacyL30(product_id=0x13, **options),
    )
    backend = device_module.LegacyHandBackend(
        "L30",
        side=HandSide.RIGHT,
        options={},
    )
    try:
        assert backend.open() == DeviceIdentity(
            model="L30",
            firmware="7.8.9",
            side=HandSide.RIGHT,
            serial_number="12345",
        )
    finally:
        backend.close()


@pytest.mark.parametrize("hand_side", ("left", "right"))
def test_l30_identity_preserves_shared_backend_position_mapping(
    monkeypatch, hand_side: str
) -> None:
    _install_model(
        monkeypatch,
        "L30",
        lambda **options: _LegacyL30(product_id=0x13, hand_side=hand_side, **options),
    )
    dispatcher = _Dispatcher()
    backend = device_module.LegacyHandBackend(
        "L30",
        side=HandSide.UNKNOWN,
        options={"dispatcher": dispatcher},
    )
    percentages = tuple((index * 7) % 101 for index in range(17))
    capabilities = get_model_capabilities("L30")
    mappings = []
    for joint in capabilities.joints:
        mapping = joint.percentage_mapping
        assert mapping is not None
        mappings.append(mapping)
    expected_raw = tuple(
        round(
            mapping.raw_closed
            + percentage / 100.0 * (mapping.raw_open - mapping.raw_closed)
        )
        for mapping, percentage in zip(mappings, percentages, strict=True)
    )
    try:
        assert backend.open().side is HandSide(hand_side)
        hand = _LegacyL30.instances[-1]

        backend.dispatch(
            HandCommand(CommandKind.ANGLES, percentages),
            claim_dispatch=lambda: True,
            mark_dispatched=lambda: None,
        )
        assert hand.angle.hardware_raw_submissions == [expected_raw]
        assert dispatcher.sent_now == [expected_raw]

        hand.angle.data = SimpleNamespace(raw_angles=expected_raw)
        reading = backend.poll(SensorType.ANGLES)
        assert reading.raw_values == expected_raw
        for actual, raw, mapping in zip(
            reading.values, expected_raw, mappings, strict=True
        ):
            span = mapping.raw_open - mapping.raw_closed
            assert actual == pytest.approx(100.0 * (raw - mapping.raw_closed) / span)
    finally:
        backend.close()


@pytest.mark.parametrize("product_model", ("O20", "O20-L", "O20-R", " o20-r "))
def test_o20_identity_accepts_exact_product_models(
    monkeypatch, product_model: str
) -> None:
    _install_model(
        monkeypatch,
        "O20",
        lambda **options: _LegacyO20(product_model=product_model, **options),
    )
    backend = device_module.LegacyHandBackend(
        "O20",
        side=HandSide.RIGHT,
        options={},
    )
    try:
        assert backend.open() == DeviceIdentity(
            model="O20",
            firmware="2.3.4",
            side=HandSide.RIGHT,
            serial_number="TEST-O20",
        )
    finally:
        backend.close()


@pytest.mark.parametrize(
    "product_model", ("not-o20", "O20-Pro", "Linkerbot O20", "O200")
)
def test_o20_identity_rejects_non_exact_product_model(
    monkeypatch, product_model: str
) -> None:
    _install_model(
        monkeypatch,
        "O20",
        lambda **options: _LegacyO20(product_model=product_model, **options),
    )
    backend = device_module.LegacyHandBackend(
        "O20",
        side=HandSide.RIGHT,
        options={},
    )

    with pytest.raises(DeviceNotReadyError, match="expected O20 device information"):
        backend.open()
    assert _LegacyO20.instances[-1].closed is True


def test_o30i_identity_reports_unknown_side_when_protocol_has_no_side(
    monkeypatch,
) -> None:
    _install_model(monkeypatch, "O30i", _LegacyO30i)
    dispatcher = _Dispatcher()
    backend = device_module.LegacyHandBackend(
        "O30i",
        side=HandSide.RIGHT,
        options={"dispatcher": dispatcher},
    )
    try:
        identity = backend.open()

        assert identity.model == "O30i"
        assert identity.firmware == "4.5.6"
        assert identity.side is HandSide.UNKNOWN
        assert identity.serial_number == "TEST-O30I"
    finally:
        backend.close()


@pytest.mark.parametrize(
    "product_model",
    ("O30", "Linkerbot O30i", "O30i-Pro", "not-o30i"),
)
def test_o30i_identity_rejects_non_exact_product_model(
    monkeypatch, product_model: str
) -> None:
    _install_model(
        monkeypatch,
        "O30i",
        lambda **options: _LegacyO30i(product_model=product_model, **options),
    )
    backend = device_module.LegacyHandBackend(
        "O30i",
        side=HandSide.RIGHT,
        options={"dispatcher": _Dispatcher()},
    )

    with pytest.raises(DeviceNotReadyError, match="expected O30i device information"):
        backend.open()
    assert _LegacyO30i.instances[-1].closed is True
