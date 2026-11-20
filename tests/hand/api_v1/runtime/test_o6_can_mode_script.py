import importlib.util
from pathlib import Path

import can
import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "examples" / "o6_can_mode.py"
spec = importlib.util.spec_from_file_location("o6_can_mode", SCRIPT)
utility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utility)


class ModeBus:
    def __init__(
        self, fd=True, *, model=b"LHO6\x00", side=b"LEFT\x00", apply=True, brs=0
    ):
        self.fd = fd
        self.model = model
        self.side = side
        self.apply = apply
        self.brs = brs
        self.sent = []
        self.pending = []
        self.drop_save_reply = False

    def send(self, message, timeout):
        self.sent.append(message)
        if message.is_fd != self.fd:
            return
        data = bytes(message.data)
        if data[:3] == b"\x35\x06\x01":
            if self.apply:
                self.fd = data[3] == 2
            return
        values = {
            bytes.fromhex("410005"): self.model,
            bytes.fromhex("41F105"): self.side,
            bytes.fromhex("B50604"): bytes((2 if self.fd else 1, self.brs, 1, 2)),
            bytes.fromhex("351E0101"): b"\x00",
        }
        if data == bytes.fromhex("351E0101") and self.drop_save_reply:
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


@pytest.mark.parametrize("source_fd,target_fd", [(True, False), (False, True)])
def test_bidirectional_switch_with_no_default_flash_save(source_fd, target_fd):
    bus = ModeBus(source_fd)
    utility.switch(bus, 0x123, source_fd, target_fd, "left", timeout=0.1)
    writes = [message for message in bus.sent if len(message.data) > 3]
    assert len(writes) == 1
    assert writes[0].is_fd is source_fd
    assert bytes(writes[0].data) == bytes((0x35, 6, 1, 2 if target_fd else 1))
    assert bus.fd is target_fd


@pytest.mark.parametrize("options", [{"model": b"O30\x00\x00"}, {"side": b"RIGHT"}])
def test_identity_mismatch_prevents_configuration_write(options):
    bus = ModeBus(**options)
    with pytest.raises(RuntimeError):
        utility.switch(bus, 1, True, False, "left", timeout=0.1)
    assert all(len(message.data) == 3 for message in bus.sent)


def test_brs_must_be_disabled_before_classic_switch():
    bus = ModeBus(brs=1)
    with pytest.raises(RuntimeError, match="BRS"):
        utility.switch(bus, 1, True, False, "left", timeout=0.1)
    assert all(len(message.data) == 3 for message in bus.sent)


def test_ignored_switch_does_not_retry_or_save():
    bus = ModeBus(apply=False)
    with pytest.raises(TimeoutError):
        utility.switch(bus, 1, True, False, "left", timeout=0.1, save=True)
    assert len([message for message in bus.sent if len(message.data) > 3]) == 1


def test_explicit_save_is_dispatched_once_and_missing_reply_is_failure():
    bus = ModeBus()
    bus.drop_save_reply = True
    with pytest.raises(TimeoutError):
        utility.switch(bus, 1, True, False, "left", timeout=0.1, save=True)
    saves = [
        message
        for message in bus.sent
        if bytes(message.data) == bytes.fromhex("351E0101")
    ]
    assert len(saves) == 1 and not saves[0].is_fd


def test_check_only_reads():
    bus = ModeBus()
    utility.inspect(bus, 1, True, "left", 0.1)
    assert all(len(message.data) == 3 for message in bus.sent)


def test_successful_save_and_already_selected_mode():
    bus = ModeBus(False)
    utility.switch(bus, 1, False, False, "left", timeout=0.1, save=True)
    writes = [message for message in bus.sent if len(message.data) > 3]
    assert [bytes(message.data) for message in writes] == [bytes.fromhex("351E0101")]


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_invalid_timeout_does_not_touch_host(value):
    with pytest.raises(SystemExit):
        utility.main(["--side", "left", "--from-mode", "canfd", "--timeout", value])
