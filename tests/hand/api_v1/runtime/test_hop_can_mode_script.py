"""Independent HOP switch validation, including O30 deferred activation."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import can
import pytest

from linkerbot_hand import HandSide, IdentityMismatchError, ProtocolError
from tests.hand.api_v1.runtime.hop_six_fake import HOPSixScript
from tests.hand.api_v1.runtime.o30_canfd_fake import (
    O30DeviceScript,
    ScriptedO30Transport,
)
from tests.hand.api_v1.runtime.o30_classic_fake import ClassicO30Transport
from tests.hand.api_v1.runtime.test_o6_classic_can import ClassicSixTransport

SCRIPT = Path(__file__).resolve().parents[4] / "examples" / "hop_can_mode.py"
spec = importlib.util.spec_from_file_location("hop_can_mode", SCRIPT)
utility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utility)


class ModeBus:
    def __init__(
        self, model="O6", fd=True, side="left", *, deferred=False, ignore=False
    ):
        self.model = model
        self.fd = fd
        self.side = side
        self.serial = "SERIAL-001"
        self.configuration = bytes((2 if fd else 1, 0, 1, 2))
        self.deferred = deferred
        self.ignore = ignore
        self.sent = []
        self.pending = []
        self.drop_save = False
        self.save_reply = b"\x00"
        self.replace_device = False
        self.change_bitrate = False

    def send(self, message, timeout):
        self.sent.append(message)
        if message.is_fd != self.fd:
            return
        data = bytes(message.data)
        if data[:3] == bytes.fromhex("350601"):
            if not self.ignore:
                self.configuration = bytes((data[3],)) + self.configuration[1:]
                if not self.deferred:
                    self.fd = data[3] == 2
            if self.replace_device:
                self.serial = "REPLACEMENT"
            if self.change_bitrate:
                self.configuration = self.configuration[:2] + bytes((2, 2))
            return
        values = {
            bytes.fromhex("410005"): self.model.encode().ljust(5, b"\x00")[:5],
            bytes.fromhex("B50604"): self.configuration,
            bytes.fromhex("351E0101"): self.save_reply,
        }
        if data == bytes.fromhex("351E0101") and self.drop_save:
            return
        self.pending.append(
            can.Message(
                arbitration_id=message.arbitration_id | 0x400,
                data=data[:3] + values[data],
                is_extended_id=False,
                is_fd=self.fd,
            )
        )

    def recv(self, timeout):
        return self.pending.pop(0) if self.pending else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def bind_identity(monkeypatch):
    def bind(bus):
        def read(channel, request_id, profile, fd, side, timeout):
            if side != bus.side:
                raise RuntimeError("Wrong device side")
            return (
                profile.model,
                bus.serial,
                bus.side,
                bus.model,
                "HOP",
                "0.0.4",
                "UID",
            )

        monkeypatch.setattr(utility, "read_identity", read)

    return bind


def before(bus, bind_identity, **options):
    bind_identity(bus)
    config, wire = utility.configuration_probe(
        bus, 0x123, (bus.fd,), timeout=0.01, reset=lambda: None
    )
    return utility.identify(
        bus, 0x123, config, wire, channel="can0", side=bus.side, timeout=0.01, **options
    )


def switch(bus, state, target, **options):
    return utility.switch(
        bus,
        0x123,
        state,
        target,
        channel="can0",
        side=bus.side,
        timeout=0.01,
        reset=lambda: None,
        **options,
    )


def writes(bus):
    return [bytes(m.data) for m in bus.sent if len(m.data) > 3]


@pytest.mark.parametrize("model", ["O6", "O30"])
@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("source,target", [(True, False), (False, True)])
def test_bidirectional_switch_without_default_flash(
    model, side, source, target, bind_identity
):
    bus = ModeBus(model, source, side)
    state = before(bus, bind_identity)
    updated = switch(bus, state, target)
    assert updated.fd is target and updated.configuration[0] == (2 if target else 1)
    assert writes(bus) == [bytes((0x35, 6, 1, 2 if target else 1))]
    assert [m.is_fd for m in bus.sent if len(m.data) > 3] == [source]


@pytest.mark.parametrize("model", ["O6", "O30"])
@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("source,target", [(True, False), (False, True)])
def test_deferred_mode_verifies_config_and_saves_via_active_old_format(
    model, save, source, target, bind_identity, capsys
):
    bus = ModeBus(model, source, deferred=True)
    state = before(bus, bind_identity)
    updated = switch(bus, state, target, save=save)
    assert updated.fd is source
    assert updated.configuration[0] == (2 if target else 1)
    assert len(writes(bus)) == (2 if save else 1)
    assert all(m.is_fd is source for m in bus.sent if len(m.data) > 3)
    assert "PENDING" in capsys.readouterr().out


@pytest.mark.parametrize("model", ["O6", "O30"])
def test_ignored_mode_does_not_retry_or_save(model, bind_identity):
    bus = ModeBus(model, ignore=True)
    with pytest.raises(RuntimeError, match="configuration"):
        switch(bus, before(bus, bind_identity), False, save=True)
    assert writes(bus) == [bytes.fromhex("35060101")]


@pytest.mark.parametrize("option", ["replace_device", "change_bitrate"])
def test_changed_identity_or_unrelated_config_prevents_save(option, bind_identity):
    bus = ModeBus()
    setattr(bus, option, True)
    with pytest.raises(RuntimeError):
        switch(bus, before(bus, bind_identity), False, save=True)
    assert writes(bus) == [bytes.fromhex("35060101")]


@pytest.mark.parametrize("drop,result", [(True, b"\x00"), (False, b"\x01")])
def test_missing_or_failed_save_ack_is_never_retried(drop, result, bind_identity):
    bus = ModeBus()
    bus.drop_save, bus.save_reply = drop, result
    with pytest.raises((TimeoutError, RuntimeError)):
        switch(bus, before(bus, bind_identity), False, save=True)
    assert writes(bus) == [bytes.fromhex("35060101"), bytes.fromhex("351E0101")]


def test_already_selected_mode_saves_only_on_explicit_request(bind_identity):
    bus = ModeBus(fd=False)
    state = before(bus, bind_identity)
    switch(bus, state, False)
    assert writes(bus) == []
    switch(bus, state, False, save=True)
    assert writes(bus) == [bytes.fromhex("351E0101")]


def test_brs_enabled_refuses_classic_without_any_writes(bind_identity):
    bus = ModeBus()
    bus.configuration = bytes((2, 1, 1, 2))
    with pytest.raises(RuntimeError, match="BRS"):
        switch(bus, before(bus, bind_identity), False)
    assert writes(bus) == []


@pytest.mark.parametrize("model", ["O30i", "L6", "O60", "OTHER"])
def test_unknown_unverified_models_never_write(model, bind_identity):
    bus = ModeBus(model)
    with pytest.raises(RuntimeError, match="Unverified"):
        before(bus, bind_identity)
    assert writes(bus) == []


@pytest.mark.parametrize(
    "expected", [{"expected_model": "O30"}, {"expected_serial": "WRONG"}]
)
def test_expected_identity_guard_prevents_write(expected, bind_identity):
    bus = ModeBus()
    with pytest.raises(RuntimeError):
        before(bus, bind_identity, **expected)
    assert writes(bus) == []


@pytest.mark.parametrize(
    "model,layout", [("O6", "documented"), ("O30", "deployed"), ("O30", "documented")]
)
@pytest.mark.parametrize("fd", [False, True])
@pytest.mark.parametrize("side", [HandSide.LEFT, HandSide.RIGHT])
def test_real_sdk_identity_layouts_and_resource_cleanup(
    model, layout, fd, side, monkeypatch
):
    script = (
        HOPSixScript("O6", side)
        if model == "O6"
        else O30DeviceScript(reported_side=side)
    )
    script.identity_layout = layout
    if fd:
        transport = ScriptedO30Transport(script)
    else:
        transport = (
            ClassicSixTransport(script)
            if model == "O6"
            else ClassicO30Transport(script)
        )
    monkeypatch.setattr(
        utility,
        "SocketCANFDTransport" if fd else "CANTransport",
        lambda channel: transport,
    )
    profile = next(p for p in utility.PROFILES if p.model == model)
    identity = utility.read_identity("can0", 1, profile, fd, side.value, 0.5)
    assert identity[:3] == (model, script.device_uid, side.value)
    assert identity[4] == "HOP"
    assert transport.get_health().state.value == "closed"
    assert not script.command_messages


@pytest.mark.parametrize("model", ["O6", "O30"])
def test_real_sdk_wrong_side_rejected_without_writes(model, monkeypatch):
    script = HOPSixScript("O6", HandSide.RIGHT) if model == "O6" else O30DeviceScript()
    transport = ScriptedO30Transport(script)
    monkeypatch.setattr(utility, "SocketCANFDTransport", lambda channel: transport)
    profile = next(p for p in utility.PROFILES if p.model == model)
    with pytest.raises((IdentityMismatchError, RuntimeError)):
        utility.read_identity("can0", 1, profile, True, "left", 0.5)
    assert not script.command_messages
    assert transport.get_health().state.value == "closed"


def test_unrecognized_o6_identity_layout_fails_closed(monkeypatch):
    script = HOPSixScript("O6", HandSide.RIGHT)
    script.identity_layout = "deployed"
    transport = ScriptedO30Transport(script)
    monkeypatch.setattr(utility, "SocketCANFDTransport", lambda channel: transport)
    with pytest.raises(ProtocolError):
        utility.read_identity("can0", 1, utility.PROFILES[0], True, "right", 0.5)
    assert not script.command_messages
    assert transport.get_health().state.value == "closed"


@pytest.mark.parametrize("deferred,expected", [(False, 0), (True, 2)])
def test_cli_distinguishes_active_and_pending_modes_and_preserves_host(
    deferred, expected, bind_identity, monkeypatch
):
    bus = ModeBus("O30", deferred=deferred)
    bind_identity(bus)
    monkeypatch.setattr(utility.can, "Bus", lambda **args: bus)
    monkeypatch.setattr(utility, "host_supports_fd", lambda channel: True)
    monkeypatch.setattr(
        utility, "configure_host", lambda *args: pytest.fail("host changed")
    )
    result = utility.main(
        [
            "--keep-host",
            "--request-id",
            "1",
            "--side",
            "left",
            "--mode",
            "can",
            "--timeout",
            "0.01",
        ]
    )
    assert result == expected
    assert writes(bus) == [bytes.fromhex("35060101")]


def test_cli_check_only_auto_detects_o30_without_writes(bind_identity, monkeypatch):
    bus = ModeBus("O30", fd=False)
    bind_identity(bus)
    monkeypatch.setattr(utility.can, "Bus", lambda **args: bus)
    monkeypatch.setattr(utility, "host_supports_fd", lambda channel: True)
    assert utility.main(["--keep-host", "--request-id", "1", "--side", "left"]) == 0
    assert writes(bus) == []


def test_read_probe_fallback_resets_host_only_after_timeout():
    bus = ModeBus(fd=False)
    resets = []
    config, wire = utility.configuration_probe(
        bus, 1, (True, False), timeout=0.01, reset=lambda: resets.append(True)
    )
    assert resets == [True] and wire is False and config[0] == 1
    assert writes(bus) == []


@pytest.mark.parametrize(
    "configuration", [b"\x02\x00\x00\x02", b"\x02\x02\x01\x02", b"\x03\x00\x01\x02"]
)
def test_invalid_config_stops_without_writes(configuration):
    bus = ModeBus()
    bus.configuration = configuration
    with pytest.raises(RuntimeError, match="configuration"):
        utility.configuration_probe(bus, 1, (True,), timeout=0.01, reset=lambda: None)
    assert writes(bus) == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--timeout", "nan"],
        ["--timeout", "0"],
        ["--request-id", "0x400"],
        ["--channel", "bad/interface"],
        ["--save"],
    ],
)
def test_invalid_cli_does_not_reconfigure_host(extra, monkeypatch):
    monkeypatch.setattr(
        utility, "configure_host", lambda *args: pytest.fail("host changed")
    )
    with pytest.raises(SystemExit):
        utility.main(["--request-id", "1", "--side", "left", *extra])


def test_classic_only_adapter_rejects_fd_target_before_device_io(monkeypatch):
    monkeypatch.setattr(utility, "host_supports_fd", lambda channel: False)
    monkeypatch.setattr(
        utility.can, "Bus", lambda **args: pytest.fail("device contacted")
    )
    with pytest.raises(SystemExit):
        utility.main(
            ["--keep-host", "--request-id", "1", "--side", "right", "--mode", "canfd"]
        )


def test_host_inspection_and_configuration_use_argument_vectors(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='[{"link_type":"can","flags":["UP"],"mtu":72}]')

    monkeypatch.setattr(utility.subprocess, "run", run)
    assert utility.host_supports_fd("can0")
    utility.configure_host("can0", False, 1000000, 5000000)
    assert calls[-2][-2:] == ["fd", "off"]
    assert all(isinstance(args, list) for args in calls)


def test_fd_host_configuration_failure_falls_back_to_classic(monkeypatch):
    calls = []

    def configure(channel, fd, bitrate, dbitrate):
        calls.append(fd)
        if fd:
            raise subprocess.CalledProcessError(1, ["ip"])

    monkeypatch.setattr(utility, "configure_host", configure)
    monkeypatch.setattr(utility, "host_supports_fd", lambda channel: False)
    with pytest.raises(SystemExit):
        utility.main(["--request-id", "1", "--side", "left", "--mode", "canfd"])
    assert calls == [True, False]
