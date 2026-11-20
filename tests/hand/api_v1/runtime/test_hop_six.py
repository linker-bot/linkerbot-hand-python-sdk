"""Public L6/O6 dual-protocol, HOP object and lifecycle regressions."""

from contextlib import closing

import pytest

from linkerbot_hand import (
    DeviceConfig,
    HandDevice,
    HandSide,
    IdentityMismatchError,
    JointFaultStatus,
    LifecycleError,
    PollingPlan,
    ProtocolError,
    ProtocolKind,
    TimeoutError,
    TransportKind,
    UnsupportedOperationError,
    ValidationError,
    get_declared_capabilities,
)
from linkerbot_hand.api.commands import CommandStage
from linkerbot_hand.protocols.hop.framing import HOPAddressing
from linkerbot_hand.protocols.hop.management import configuration_transaction
from linkerbot_hand.protocols.hop.six_axis import POSITIONS
from linkerbot_hand.protocols.registry import create_protocol_backend
from tests.hand.api_v1.runtime.hop_six_fake import HOPSixScript
from tests.hand.api_v1.runtime.o30_canfd_fake import ScriptedO30Transport


def make_hand(
    model="L6", side=HandSide.RIGHT, request_id=1, sensor_variant="NO_SENSOR", brs=False
):
    script = HOPSixScript(model, side, request_id, sensor_variant)
    transport = ScriptedO30Transport(script)
    hand = HandDevice(
        model=model,
        side=side,
        protocol="hop",
        transport=transport,
        config=DeviceConfig(hop_request_id=request_id, hop_brs=brs),
    )
    return script, transport, hand


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_dual_protocol_profiles_and_default_ids(model, side):
    legacy = get_declared_capabilities(model, side=side)
    hop = get_declared_capabilities(model, side=side, protocol="hop")
    assert legacy.protocol is ProtocolKind.LEGACY
    assert legacy.transport.kind is TransportKind.CAN
    assert hop.protocol is ProtocolKind.HOP
    assert hop.transport.kind is TransportKind.CAN_FD
    assert hop.joint_names == legacy.joint_names
    assert create_protocol_backend(hop).route_keys[0].arbitration_id == 0x401
    script, _, hand = make_hand(model, side)
    assert not script.sent_messages
    with closing(hand.open()):
        assert hand.get_device_info(timeout=1).reported_side is side
        assert hand.get_angles(timeout=1).raw == POSITIONS
        assert all(
            m.arbitration_id == 1 and m.frame_type == 0x04 for m in script.sent_messages
        )


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize(
    "name,mi,rts",
    [
        ("angles", 1, False),
        ("speeds", 2, False),
        ("configured_speeds", 2, True),
        ("configured_accelerations", 3, True),
        ("currents", 4, False),
        ("current_limits", 4, True),
        ("voltages", 5, False),
        ("torques", 6, False),
        ("torques_limits", 6, True),
        ("temperatures", 7, False),
        ("motion_times", 8, True),
        ("stall_detection_times", 9, True),
        ("stall_thresholds", 10, True),
        ("stall_hold_currents", 11, True),
    ],
)
def test_reads_actual_and_configured_views(model, name, mi, rts):
    script, _, hand = make_hand(model)
    with closing(hand.open()):
        sample = getattr(hand, f"get_{name}")(timeout=1)
        expected = tuple((100 if rts else 0) + pos for pos in POSITIONS)
        assert sample.raw == expected
        assert script.sent_messages[-1].data[:3] == bytes(
            [mi | (0x80 if rts else 0), 10, 5]
        )


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize(
    "name",
    [
        "angles",
        "speeds",
        "accelerations",
        "current_limits",
        "torques_limits",
        "motion_times",
        "stall_detection_times",
        "stall_thresholds",
        "stall_hold_currents",
    ],
)
def test_write_preserves_holes_and_completes_all_ranges(model, name):
    script, _, hand = make_hand(model)
    with closing(hand.open()):
        before = dict(script.targets)
        handle = getattr(hand, f"set_{name}")([0, 1, 2, 3, 254, 255])
        handle.wait(1)
        assert handle.stage is CommandStage.ACKNOWLEDGED
        messages = script.command_messages[-2:]
        assert [m.data[1:3] for m in messages] == [b"\x05\x01", b"\x0a\x05"]
        mi = messages[0].data[0] & 0x7F
        assert script.targets[mi][5] == 1
        assert script.targets[mi][10:15] == bytes([0, 2, 3, 254, 255])
        assert script.targets[mi][:5] == before[mi][:5]
        assert script.targets[mi][6:10] == before[mi][6:10]
        if name == "angles":
            assert hand.get_snapshot().angles.raw == (0, 1, 2, 3, 254, 255)


def test_l6_pid_is_u16_and_torque_zero_has_no_legacy_mode_write():
    script, _, hand = make_hand()
    with closing(hand.open()):
        for name, mi in (("position_kp", 0x21), ("position_kd", 0x23)):
            getattr(hand, f"set_{name}")([0, 1, 2, 3, 4, 255]).wait(1)
            assert script.command_messages[-2].data[1:3] == bytes([10, 2])
            assert script.command_messages[-1].data[1:3] == bytes([20, 10])
            assert getattr(hand, f"get_{name}")(timeout=1).raw == (0, 1, 2, 3, 4, 255)
            with pytest.raises(ValidationError):
                getattr(hand, f"set_{name}")([256] * 6)
        hand.set_torques([0] * 6).wait(1)
        assert all(m.data[0] & 0x7F == 6 for m in script.command_messages[-2:])


@pytest.mark.parametrize(
    "model,name",
    [
        ("L6", "clear_faults"),
        ("L6", "set_torque_kd"),
        ("L6", "set_limit_compensations"),
        ("O6", "set_position_kp"),
        ("O6", "set_torques"),
        ("L6", "enable"),
        ("O6", "calibrate"),
    ],
)
def test_undefined_operations_fail_without_wire_io(model, name):
    script, _, hand = make_hand(model)
    with closing(hand.open()):
        count = len(script.sent_messages)
        with pytest.raises(UnsupportedOperationError):
            getattr(hand, name)(*[([0] * 6)] if name.startswith("set_") else [])
        assert len(script.sent_messages) == count


def test_hop_fault_bits_and_incomplete_sample_does_not_replace_cache():
    script, _, hand = make_hand()
    script.actual[12] = bytes([0xCF] * 36)
    with closing(hand.open()):
        sample = hand.get_faults(timeout=1)
        assert all(isinstance(s, JointFaultStatus) for s in sample.statuses)
        assert sample.statuses[0].over_current
        before = hand.get_snapshot().angles
        script.drop_range = (1, 10)
        with pytest.raises(TimeoutError):
            hand.get_angles(timeout=0.3)
        assert hand.get_snapshot().angles.raw == before.raw
        assert hand.get_snapshot().angles.meta.sequence == before.meta.sequence


def test_second_write_range_failure_is_not_acknowledged():
    script, _, hand = make_hand()
    with closing(hand.open()):
        script.reject_range = (1, 10)
        handle = hand.set_angles([42] * 6)
        assert handle.wait(1) is CommandStage.FAILED
        assert handle.error is not None
        assert script.targets[1][5] == 42
        assert script.targets[1][10] != 42
        assert hand.get_snapshot().angles.raw == POSITIONS


@pytest.mark.parametrize(
    "variant,fields",
    [("TASHAN_GATHER", ("Fn", "Ft", "Fd", "Px")), ("TSSP_JZG", ("FnS", "Fn"))],
)
@pytest.mark.parametrize("model", ["L6", "O6"])
def test_shared_sensor_descriptor_values(model, variant, fields):
    _, _, hand = make_hand(model, sensor_variant=variant)
    with closing(hand.open()):
        sample = hand.get_force(*(f.lower() for f in fields), timeout=2)
        assert len(sample.sensors) == 5
        assert tuple(f.semantic for f in sample.sensors[0].fields) == fields


@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("set_can_id", (0x123,), {}),
        ("set_can_brs", (True,), {}),
        ("set_can_bitrates", (), {"data_bitrate": 2_000_000}),
    ],
)
def test_configuration_rejected_while_polling_and_can_continue(name, args, kwargs):
    script, _, hand = make_hand()
    with closing(hand.open()):
        hand.start_polling(PollingPlan(angles_hz=30))
        assert hand.get_can_configuration().request_id == 1
        with pytest.raises(LifecycleError, match="stop_polling"):
            getattr(hand, name)(*args, **kwargs)
        assert not any(m.data[0] & 0x7F == 0x35 for m in script.command_messages)
        assert hand.get_angles(timeout=1).raw == POSITIONS
        hand.stop_polling()
        getattr(hand, name)(*args, **kwargs)
        assert not hand.get_snapshot().angles.meta.valid


@pytest.mark.parametrize(
    "name,args,kwargs,si,payload",
    [
        ("set_can_id", (0x123,), {}, 0, b"\x23\x01"),
        ("set_can_brs", (True,), {}, 7, b"\x01"),
        ("set_can_brs", (False,), {}, 7, b"\x00"),
        (
            "set_can_bitrates",
            (),
            {"nominal_bitrate": 500_000, "data_bitrate": 2_000_000},
            8,
            b"\x03\x05",
        ),
        ("set_can_bitrates", (), {"data_bitrate": 5_000_000}, 9, b"\x02"),
    ],
)
@pytest.mark.parametrize("model", ["L6", "O6"])
def test_configuration_single_dispatch_no_ack_and_closed(
    name, args, kwargs, si, payload, model
):
    script, _, hand = make_hand(model)
    hand.open()
    try:
        config = hand.get_can_configuration()
        assert (config.request_id, config.response_id, config.brs) == (1, 0x401, False)
        assert config.nominal_bitrate == 1_000_000
        count = len(script.command_messages)
        getattr(hand, name)(*args, **kwargs)
        assert len(script.command_messages) == count + 1
        assert (
            script.command_messages[-1].data
            == bytes([0xB5, si, len(payload)]) + payload
        )
        assert not hand.get_snapshot().angles.meta.valid
    finally:
        hand.close()


@pytest.mark.parametrize("model", ["L6", "O6"])
def test_new_config_reconnect_and_save(model):
    script, transport, hand = make_hand(model)
    hand.open()
    hand.set_can_id(0x123)
    new = HandDevice(
        model=model,
        protocol="hop",
        side="right",
        transport=transport,
        config=DeviceConfig(hop_request_id=0x123, hop_brs=True),
    )
    with closing(new.open()):
        assert new.get_can_configuration().request_id == 0x123
        new.save_parameters().wait(1)
        assert script.command_messages[-1].data == b"\xb5\x1e\x01\x01"
        assert all(
            m.frame_type == 0x0C
            for m in script.sent_messages
            if m.arbitration_id == 0x123
        )


@pytest.mark.parametrize("value", [-1, 0x3FF, True, 1.0])
def test_invalid_id_has_no_side_effects(value):
    script, _, hand = make_hand()
    with closing(hand.open()):
        count = len(script.sent_messages)
        with pytest.raises(ValidationError):
            hand.set_can_id(value)
        assert len(script.sent_messages) == count
        assert hand.get_angles(timeout=1).meta.valid


@pytest.mark.parametrize(
    "nominal_code,data_code,nominal,data",
    [
        (1, 1, 1_000_000, 8_000_000),
        (3, 5, 500_000, 2_000_000),
        (8, 13, 100_000, 100_000),
        (2, 6, 800_000, 1_000_000),
    ],
)
def test_configuration_bitrate_enums_roundtrip(nominal_code, data_code, nominal, data):
    script = HOPSixScript()
    script.configuration[8:10] = bytes([nominal_code, data_code])
    tx = configuration_transaction(HOPAddressing(1, 0x401))
    while tx is not None:
        progress = tx.consume(script.respond(tx.request_messages[0])[0])
        result = progress.result
        tx = tx.build_next_transaction()
    assert result.nominal_bitrate == nominal
    assert result.data_bitrate == data


def test_side_mismatch_rejected_before_baseline():
    script, transport, _ = make_hand(side=HandSide.LEFT)
    hand = HandDevice(model="L6", side="right", protocol="hop", transport=transport)
    with pytest.raises(IdentityMismatchError):
        hand.open()
    assert not any(m.data[0] & 0x7F == 1 for m in script.sent_messages)


def test_reconfiguration_serializes_with_poll_start_and_rejects_concurrent_work():
    import threading

    from linkerbot_hand import ClosedError, DeviceNotReadyError

    script, _, hand = make_hand()
    entered = threading.Event()
    release = threading.Event()
    attempting_poll = threading.Event()
    errors = []
    original_write = script._write

    def block_configuration(mi, si, length, payload, *, rts=False):
        if mi == 0x35:
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test release was not signaled")
        return original_write(mi, si, length, payload, rts=rts)

    script._write = block_configuration
    hand.open()

    def change():
        try:
            hand.set_can_brs(True)
        except Exception as error:  # noqa: BLE001 - propagate worker failures to test
            errors.append(error)

    def poll():
        attempting_poll.set()
        try:
            hand.start_polling(PollingPlan(angles_hz=30))
        except ClosedError:
            return
        errors.append(RuntimeError("polling started during reconfiguration"))

    writer = threading.Thread(target=change)
    starter = threading.Thread(target=poll)
    try:
        writer.start()
        assert entered.wait(1)
        with pytest.raises(DeviceNotReadyError):
            hand.set_angles([0] * 6)
        with pytest.raises(DeviceNotReadyError):
            hand.get_angles(timeout=0.2)
        starter.start()
        assert attempting_poll.wait(1)
        release.set()
        writer.join(2)
        starter.join(2)
        assert not writer.is_alive() and not starter.is_alive()
        assert not errors
        assert script.sent_messages[-1].data == b"\xb5\x07\x01\x01"
    finally:
        release.set()
        writer.join(2)
        if starter.ident is not None:
            starter.join(2)
        hand.close()


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("set_can_id", (0x123,), {}),
        ("set_can_brs", (True,), {}),
        ("set_can_bitrates", (), {"data_bitrate": 2_000_000}),
    ],
)
def test_configuration_switch_rejects_reads_before_old_waiters_are_notified(
    monkeypatch, model, name, args, kwargs
):
    import threading

    from linkerbot_hand import DeviceNotReadyError, ErrorCode
    from linkerbot_hand.capabilities.types import TelemetrySemantic

    script, _, hand = make_hand(model)
    hand.open()
    runtime = hand._runtime
    coordinator = runtime._transactions
    original_read = script._read
    original_request = coordinator.request
    inflight_started = threading.Event()
    finish_inflight = threading.Event()
    queued = threading.Event()
    notifying = threading.Event()
    finish_notifications = threading.Event()
    errors = {}
    count = len(script.sent_messages)

    def block_inflight_read(mi, si, length, *, rts):
        if mi == 1 and si == 5:
            inflight_started.set()
            assert finish_inflight.wait(3), "in-flight read was not released"
        return original_read(mi, si, length, rts=rts)

    def observe_request(key, **request_kwargs):
        waiter = original_request(key, **request_kwargs)
        if key is TelemetrySemantic.CURRENTS:
            original_fail = waiter.fail

            def pause_notification(error):
                original_fail(error)
                notifying.set()
                assert finish_notifications.wait(3), "notification was not released"

            monkeypatch.setattr(waiter, "fail", pause_notification)
            queued.set()
        return waiter

    def call(label, function):
        try:
            function()
        except Exception as error:  # noqa: BLE001 - collect worker failures
            errors[label] = error

    monkeypatch.setattr(script, "_read", block_inflight_read)
    monkeypatch.setattr(coordinator, "request", observe_request)
    threads = [
        threading.Thread(
            target=call, args=("inflight", lambda: hand.get_angles(timeout=3))
        ),
        threading.Thread(
            target=call, args=("queued", lambda: hand.get_currents(timeout=3))
        ),
        threading.Thread(
            target=call,
            args=("writer", lambda: getattr(hand, name)(*args, timeout=2, **kwargs)),
        ),
    ]
    try:
        threads[0].start()
        assert inflight_started.wait(1)
        threads[1].start()
        assert queued.wait(1)
        command = hand.set_speeds([25] * 6)
        threads[2].start()
        assert notifying.wait(1)
        # The actor is still finishing the old read, while its caller and the
        # queued caller are being failed. New readers must already be rejected.
        for read in (
            hand.get_angles,
            hand.get_currents,
            hand.get_can_configuration,
            hand.get_device_info,
        ):
            with pytest.raises(DeviceNotReadyError, match="CAN settings are changing"):
                read(timeout=0.1)
        with pytest.raises(DeviceNotReadyError):
            hand.set_angles([0] * 6)
        assert command.stage is CommandStage.FAILED
        assert command.error is not None
        assert command.error.code is ErrorCode.DEVICE_NOT_READY
        finish_notifications.set()
        finish_inflight.set()
        for thread in threads:
            thread.join(3)
        assert all(not thread.is_alive() for thread in threads)
        assert "writer" not in errors
        assert set(errors) == {"inflight", "queued"}
        assert all(isinstance(error, DeviceNotReadyError) for error in errors.values())
        messages = script.sent_messages[count:]
        assert [(m.data[0] & 0x7F, m.data[1]) for m in messages[:2]] == [
            (1, 5),
            (1, 10),
        ]
        assert len(messages) == 3
        assert len(script.command_messages) == 1
        assert messages[-1].data[0] == 0xB5
        assert not hand.get_snapshot().angles.meta.valid
    finally:
        finish_notifications.set()
        finish_inflight.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(3)
        hand.close()


def test_configuration_preparation_failure_closes_the_session(monkeypatch):
    from linkerbot_hand import ClosedError

    script, _, hand = make_hand()
    hand.open()
    commands = hand._runtime._commands
    original_fail_pending = commands.fail_pending
    failure = RuntimeError("configuration preparation failed")
    calls = 0

    def fail_once(error):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure
        return original_fail_pending(error)

    monkeypatch.setattr(commands, "fail_pending", fail_once)
    try:
        with pytest.raises(RuntimeError) as raised:
            hand.set_can_brs(True)
        assert raised.value is failure
        assert not script.command_messages
        assert not hand.get_snapshot().angles.meta.valid
        with pytest.raises(ClosedError):
            hand.get_angles(timeout=0.1)
    finally:
        hand.close()


@pytest.mark.parametrize("model", ["L6", "O6"])
@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("set_can_id", (0x123,), {}),
        ("set_can_brs", (True,), {}),
        ("set_can_bitrates", (), {"data_bitrate": 2_000_000}),
    ],
)
def test_configuration_timeout_before_dispatch_does_not_send_later(
    model, name, args, kwargs
):
    import threading

    script, _, hand = make_hand(model)
    hand.open()
    entered = threading.Event()
    read_errors = []
    original_read = script._read

    def dropped_read(mi, si, length, *, rts):
        if mi == 1:
            entered.set()
            return ()
        return original_read(mi, si, length, rts=rts)

    script._read = dropped_read

    def read():
        try:
            hand.get_angles(timeout=1)
        except Exception as error:  # noqa: BLE001 - propagate worker failures to test
            read_errors.append(error)

    reader = threading.Thread(target=read)
    try:
        reader.start()
        assert entered.wait(1)
        with pytest.raises(TimeoutError):
            getattr(hand, name)(*args, timeout=0.01, **kwargs)
        reader.join(1)
        assert not reader.is_alive()
        assert read_errors
        assert not any(m.data[0] & 0x7F == 0x35 for m in script.command_messages)
    finally:
        hand.close()
        reader.join(1)


@pytest.mark.parametrize(
    "name,args,kwargs",
    [
        ("set_can_brs", (1,), {}),
        ("set_can_brs", (None,), {}),
        ("set_can_bitrates", (), {}),
        ("set_can_bitrates", (), {"nominal_bitrate": 123456}),
        ("set_can_bitrates", (), {"data_bitrate": True}),
        ("set_can_bitrates", (), {"nominal_bitrate": 1_000_000.0}),
        ("set_can_id", (1,), {"timeout": 0}),
    ],
)
def test_invalid_configuration_arguments_keep_connection_usable(name, args, kwargs):
    script, _, hand = make_hand()
    with closing(hand.open()):
        count = len(script.sent_messages)
        with pytest.raises(ValidationError):
            getattr(hand, name)(*args, **kwargs)
        assert len(script.sent_messages) == count
        assert hand.get_angles(timeout=1).raw == POSITIONS


@pytest.mark.parametrize("index,value", [(6, 0), (7, 2), (8, 0), (9, 14)])
def test_invalid_device_configuration_is_reported_as_protocol_error(index, value):
    script, _, hand = make_hand()
    script.configuration[index] = value
    with closing(hand.open()):
        with pytest.raises(ProtocolError):
            hand.get_can_configuration()
        assert hand.get_angles(timeout=1).raw == POSITIONS


def test_installed_empty_polling_plan_also_blocks_configuration():
    script, _, hand = make_hand()
    with closing(hand.open()):
        hand.start_polling(PollingPlan())
        with pytest.raises(LifecycleError, match="stop_polling"):
            hand.set_can_brs(True)
        assert not script.command_messages
        hand.stop_polling()
        hand.set_can_brs(True)


@pytest.mark.parametrize(
    "model,protocol", [("L6", None), ("O6", None), ("O30", None), ("L20", None)]
)
def test_device_register_api_is_not_assumed_for_other_profiles(model, protocol):
    hand = HandDevice(model=model, side="right", protocol=protocol, transport=object())
    for fn in [
        lambda: hand.get_can_configuration(),
        lambda: hand.set_can_id(1),
        lambda: hand.set_can_brs(True),
        lambda: hand.set_can_bitrates(data_bitrate=2_000_000),
    ]:
        with pytest.raises(UnsupportedOperationError):
            fn()


@pytest.mark.parametrize(
    "model,protocol", [("L6", "auto"), ("O30", "legacy"), ("L20", "hop")]
)
def test_protocol_selection_rejects_unsupported_profiles(model, protocol):
    with pytest.raises(ValidationError):
        HandDevice(model=model, side="right", protocol=protocol, transport=object())
