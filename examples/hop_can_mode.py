"""Inspect/switch HOP CAN modes for verified device profiles (O6, O30).

Examples (stop other command producers on this interface first):
  python examples/hop_can_mode.py --request-id 1 --side right
  python examples/hop_can_mode.py --request-id 1 --side right --mode can
  python examples/hop_can_mode.py --request-id 1 --side right --mode canfd --save

Requires python-can, the installed Hand SDK, Linux iproute2 and sudo -n.
By default the host interface is reconfigured to receive CAN FD and classic CAN.
--keep-host skips host changes and requires an already configured interface.
Without --mode, only device reads are issued. No motion, restart, ID, bitrate or
BRS writes are issued. Mode and Flash writes are sent at most once. Flash save
may persist other device settings too; power-cycle persistence is not tested.

Add future models only after verifying their mode/save register contract and
SDK identity support; register their product prefixes in PROFILES and add tests.
Unknown products are never switched. Configuration and actual reply format are
reported separately because some firmware applies a new mode after restart.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from contextlib import closing
from time import monotonic
from typing import NamedTuple

import can

from linkerbot_hand import DeviceConfig, HandDevice, TransportKind
from linkerbot_transport.can import CANTransport
from linkerbot_transport.canfd import SocketCANFDTransport


class Profile(NamedTuple):
    model: str
    products: tuple[str, ...]
    configuration_read: bytes = bytes.fromhex("B50604")
    mode_header: bytes = bytes.fromhex("350601")
    save_request: bytes = bytes.fromhex("351E0101")
    save_success: bytes = b"\x00"


# These two profiles share a verified register contract, not an assumption that
# every HOP product uses the same CAN controller configuration objects.
PROFILES = (Profile("O6", ("O6", "LHO6")), Profile("O30", ("O30",)))


class State(NamedTuple):
    profile: Profile
    configuration: bytes
    fd: bool
    identity: tuple


def wire_name(fd):
    return "canfd" if fd else "can"


def exchange(bus, request_id, data, *, can_fd, timeout, response_fd=None):
    # Discard old responses before sending. Exact endpoint/header matching alone
    # cannot distinguish a stale reply to an earlier identical query.
    deadline = monotonic() + timeout
    while bus.recv(timeout=0) is not None:
        if monotonic() >= deadline:
            raise RuntimeError("Bus remains busy; stop other command producers")
    bus.send(
        can.Message(
            arbitration_id=request_id,
            data=data,
            is_extended_id=False,
            is_fd=can_fd,
            check=True,
        ),
        timeout=timeout,
    )
    deadline = monotonic() + timeout
    while (remaining := deadline - monotonic()) > 0:
        reply = bus.recv(timeout=remaining)
        if reply is None:
            break
        if (
            reply.is_error_frame
            or reply.is_remote_frame
            or reply.is_extended_id
            or reply.arbitration_id != request_id | 0x400
            or (response_fd is not None and reply.is_fd != response_fd)
        ):
            continue
        payload = bytes(reply.data)
        if len(payload) < 3:
            continue
        if payload[0] & 0x7F == 0x4F:
            raise RuntimeError(f"Device communication error: {payload.hex()}")
        if payload[:3] != data[:3]:
            continue
        size = payload[2]
        if len(payload) < 3 + size:
            raise RuntimeError("Truncated HOP response")
        return payload[3 : 3 + size], reply.is_fd
    raise TimeoutError(f"No matching {wire_name(can_fd)} reply for {data.hex()}")


def configure_host(channel, fd, bitrate, dbitrate):
    subprocess.run(
        ["sudo", "-n", "ip", "link", "set", "dev", channel, "down"], check=True
    )
    command = [
        "sudo",
        "-n",
        "ip",
        "link",
        "set",
        "dev",
        channel,
        "type",
        "can",
        "bitrate",
        str(bitrate),
    ]
    command += ["dbitrate", str(dbitrate), "fd", "on"] if fd else ["fd", "off"]
    subprocess.run(command, check=True)
    subprocess.run(
        ["sudo", "-n", "ip", "link", "set", "dev", channel, "up"], check=True
    )


def reset_host(channel):
    # Only called for a failed read probe, never to repeat a device write.
    for state in ("down", "up"):
        subprocess.run(
            ["sudo", "-n", "ip", "link", "set", "dev", channel, state], check=True
        )


def host_supports_fd(channel):
    result = subprocess.run(
        ["ip", "-details", "-json", "link", "show", "dev", channel],
        check=True,
        capture_output=True,
        text=True,
    )
    links = json.loads(result.stdout)
    if len(links) != 1 or links[0].get("link_type") != "can":
        raise RuntimeError("Expected one SocketCAN interface")
    if "UP" not in links[0].get("flags", ()):
        raise RuntimeError("CAN interface must be UP")
    return links[0]["mtu"] == 72


def read_identity(channel, request_id, profile, fd, side, timeout):
    """Reuse SDK layout detection and identity validation; no device writes."""
    transport_type = SocketCANFDTransport if fd else CANTransport
    kind = TransportKind.CAN_FD if fd else TransportKind.CAN
    with (
        closing(transport_type(channel)) as transport,
        closing(
            HandDevice(
                model=profile.model,
                side=side,
                protocol="hop",
                transport=transport,
                config=DeviceConfig(
                    hop_request_id=request_id,
                    hop_transport=kind,
                    identity_timeout_s=timeout,
                ),
            )
        ) as hand,
    ):
        hand.open()
        info = hand.get_device_info(timeout=max(5.0, timeout))
        if (
            info.model != profile.model
            or info.reported_side is None
            or info.reported_side.value != side
            or not info.serial_number
            or info.protocol_name != "HOP"
        ):
            raise RuntimeError("Device identity/side/protocol was not verified")
        return (
            info.model,
            info.serial_number,
            side,
            info.product_code,
            info.protocol_name,
            info.protocol_version,
            info.microcontroller_uid,
        )


def identify(
    bus,
    request_id,
    configuration,
    fd,
    *,
    channel,
    side,
    timeout,
    expected_model=None,
    expected_serial=None,
):
    product, _ = exchange(
        bus,
        request_id,
        bytes.fromhex("410005"),
        can_fd=fd,
        timeout=timeout,
        response_fd=fd,
    )
    name = product.split(b"\x00", 1)[0].decode("ascii").strip().upper()
    profile = next(
        (
            p
            for p in PROFILES
            if any(
                name == prefix or name.startswith(prefix + "-") for prefix in p.products
            )
        ),
        None,
    )
    if profile is None:
        raise RuntimeError(f"Unverified product {name!r}; no device writes")
    if expected_model and profile.model != expected_model:
        raise RuntimeError("Device differs from --model; no device writes")
    identity = read_identity(channel, request_id, profile, fd, side, timeout)
    if expected_serial and identity[1] != expected_serial:
        raise RuntimeError("Device differs from --serial; no device writes")
    print(
        f"{profile.model} {side} serial={identity[1]} reply={wire_name(fd)} "
        f"mode={configuration[0]} BRS={configuration[1]}",
        flush=True,
    )
    return State(profile, configuration, fd, identity)


def configuration_probe(
    bus, request_id, formats, *, timeout, reset, request=b"\xb5\x06\x04"
):
    for fd in dict.fromkeys(formats):
        try:
            configuration, wire = exchange(
                bus, request_id, request, can_fd=fd, timeout=timeout
            )
        except TimeoutError:
            reset()
            continue
        if (
            len(configuration) != 4
            or configuration[0] not in (1, 2)
            or configuration[1] not in (0, 1)
            or not 1 <= configuration[2] <= 8
            or not 1 <= configuration[3] <= 13
        ):
            raise RuntimeError(f"Invalid CAN configuration: {configuration.hex()}")
        return configuration, wire
    raise TimeoutError("Device mode could not be verified; no automatic write retry")


def switch(
    bus, request_id, before, target_fd, *, channel, side, timeout, reset, save=False
):
    target = 2 if target_fd else 1
    if not target_fd and before.configuration[1]:
        raise RuntimeError("Disable BRS separately before selecting classic CAN")
    profile = before.profile
    if before.configuration[0] != target:
        bus.send(
            can.Message(
                arbitration_id=request_id,
                is_extended_id=False,
                data=profile.mode_header + bytes((target,)),
                is_fd=before.fd,
                check=True,
            ),
            timeout=timeout,
        )
        print(
            "Mode write sent ONCE; a missing reply does not imply no change", flush=True
        )

    def verify(formats):
        configuration, wire = configuration_probe(
            bus,
            request_id,
            formats,
            timeout=timeout,
            reset=reset,
            request=profile.configuration_read,
        )
        if configuration != bytes((target,)) + before.configuration[1:]:
            raise RuntimeError(
                "Target configuration/BRS/bitrates not confirmed; do not repeat write"
            )
        state = identify(
            bus,
            request_id,
            configuration,
            wire,
            channel=channel,
            side=side,
            timeout=timeout,
            expected_model=profile.model,
        )
        if state.identity != before.identity:
            raise RuntimeError("Device identity changed; do not save or repeat writes")
        return state

    updated = verify((target_fd, before.fd))
    if save:
        result, _ = exchange(
            bus,
            request_id,
            profile.save_request,
            can_fd=updated.fd,
            timeout=timeout,
            response_fd=updated.fd,
        )
        if result != profile.save_success:
            raise RuntimeError("Flash completion not confirmed; do not repeat save")
        print("Flash save confirmed ONCE; power-cycle persistence untested", flush=True)
        updated = verify((target_fd, updated.fd))
    if updated.fd != target_fd:
        print(
            "PENDING: target register verified, active format unchanged. "
            "No automatic restart; verify after a controlled restart.",
            flush=True,
        )
    else:
        print("ACTIVE: target register and actual reply format verified", flush=True)
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--request-id", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--side", choices=("left", "right"), required=True)
    parser.add_argument("--model", choices=[p.model for p in PROFILES])
    parser.add_argument("--serial")
    parser.add_argument("--from-mode", choices=("auto", "can", "canfd"), default="auto")
    parser.add_argument("--mode", choices=("can", "canfd"))
    parser.add_argument(
        "--save", action="store_true", help="Explicitly persist verified settings"
    )
    parser.add_argument(
        "--keep-host",
        action="store_true",
        help="Never reconfigure/reset host interface",
    )
    parser.add_argument("--bitrate", type=int, default=1_000_000)
    parser.add_argument("--dbitrate", type=int, default=5_000_000)
    parser.add_argument("--timeout", type=float, default=0.5)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", args.channel):
        parser.error("invalid SocketCAN interface name")
    if not 0 <= args.request_id <= 0x3FE:
        parser.error("request ID must be within 0..0x3FE")
    if not 0 < args.bitrate <= 1_000_000 or not 0 < args.dbitrate <= 8_000_000:
        parser.error("invalid bitrates")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    if args.save and args.mode is None:
        parser.error("--save requires --mode")
    if not args.keep_host:
        try:
            configure_host(args.channel, True, args.bitrate, args.dbitrate)
        except subprocess.CalledProcessError:
            configure_host(args.channel, False, args.bitrate, args.dbitrate)
    fd_capable = host_supports_fd(args.channel)
    if not fd_capable and (args.mode == "canfd" or args.from_mode == "canfd"):
        parser.error(
            "CAN FD requires an FD-capable configured adapter; device not changed"
        )
    formats = (
        (False, True) if args.from_mode == "auto" else (args.from_mode == "canfd",)
    )
    formats = tuple(fd for fd in formats if fd_capable or not fd)
    reset = (lambda: None) if args.keep_host else (lambda: reset_host(args.channel))
    with can.Bus(interface="socketcan", channel=args.channel, fd=fd_capable) as bus:
        configuration, wire = configuration_probe(
            bus, args.request_id, formats, timeout=args.timeout, reset=reset
        )
        state = identify(
            bus,
            args.request_id,
            configuration,
            wire,
            channel=args.channel,
            side=args.side,
            timeout=args.timeout,
            expected_model=args.model,
            expected_serial=args.serial,
        )
        if args.mode is not None:
            state = switch(
                bus,
                args.request_id,
                state,
                args.mode == "canfd",
                channel=args.channel,
                side=args.side,
                timeout=args.timeout,
                reset=reset,
                save=args.save,
            )
    if args.mode is not None and not args.keep_host:
        configure_host(args.channel, state.fd, args.bitrate, args.dbitrate)
    return 2 if args.mode is not None and state.fd != (args.mode == "canfd") else 0


if __name__ == "__main__":
    raise SystemExit(main())
