"""Standalone O6 HOP CAN mode switch; no motion and no default Flash writes."""

from __future__ import annotations

import argparse
import math
import subprocess
from time import monotonic

import can


def exchange(bus, request_id, data, *, can_fd, timeout=0.5, response_fd=None):
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
        control, _start, size = payload[:3]
        if control & 0x7F == 0x4F:
            raise RuntimeError(f"Device communication error: {payload.hex()}")
        if payload[:3] != data[:3]:
            continue
        if len(payload) < 3 + size:
            raise RuntimeError("Truncated HOP response")
        return payload[3 : 3 + size], reply.is_fd
    raise TimeoutError(f"No matching reply for {data.hex()}")


def inspect(bus, request_id, can_fd, side, timeout):
    product, wire = exchange(
        bus, request_id, bytes.fromhex("410005"), can_fd=can_fd, timeout=timeout
    )
    model = product.split(b"\x00", 1)[0].decode("ascii")
    if model.upper() not in {"O6", "LHO6"}:
        raise RuntimeError(f"Expected O6, received {model!r}; device not changed")
    reported_side, _ = exchange(
        bus,
        request_id,
        bytes.fromhex("41F105"),
        can_fd=can_fd,
        timeout=timeout,
        response_fd=wire,
    )
    if reported_side.rstrip(b"\x00").decode("ascii").lower() != side:
        raise RuntimeError("Device side mismatch; device not changed")
    configuration, _ = exchange(
        bus,
        request_id,
        bytes.fromhex("B50604"),
        can_fd=can_fd,
        timeout=timeout,
        response_fd=wire,
    )
    mode, brs, nominal, data = configuration
    if (
        mode not in (1, 2)
        or brs not in (0, 1)
        or not 1 <= nominal <= 8
        or not 1 <= data <= 13
    ):
        raise RuntimeError(f"Invalid device configuration: {configuration.hex()}")
    print(
        f"O6 {side}: reply={'canfd' if wire else 'can'}, mode={mode}, BRS={brs}",
        flush=True,
    )
    return configuration, wire


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
    if fd:
        command += ["dbitrate", str(dbitrate), "fd", "on"]
    else:
        command += ["fd", "off"]
    subprocess.run(command, check=True)
    subprocess.run(
        ["sudo", "-n", "ip", "link", "set", "dev", channel, "up"], check=True
    )


def switch(bus, request_id, source_fd, target_fd, side, *, timeout=0.5, save=False):
    configuration, wire = inspect(bus, request_id, source_fd, side, timeout)
    if wire != source_fd:
        raise RuntimeError("Actual reply format differs from --from-mode")
    target = 2 if target_fd else 1
    if not target_fd and configuration[1]:
        raise RuntimeError("BRS must be disabled before switching to classic CAN")
    if configuration[0] != target or wire != target_fd:
        bus.send(
            can.Message(
                arbitration_id=request_id,
                data=bytes((0x35, 6, 1, target)),
                is_extended_id=False,
                is_fd=source_fd,
                check=True,
            ),
            timeout=timeout,
        )
        print(
            "Mode write sent once; an absent reply does not imply no change", flush=True
        )
    updated, actual_fd = inspect(bus, request_id, target_fd, side, timeout)
    if updated[0] != target or actual_fd != target_fd:
        raise RuntimeError("Target mode or active reply format was not verified")
    if save:
        result, _ = exchange(
            bus,
            request_id,
            bytes.fromhex("351E0101"),
            can_fd=target_fd,
            timeout=timeout,
            response_fd=target_fd,
        )
        if result != b"\x00":
            raise RuntimeError(
                "Save completion not confirmed; do not repeat automatically"
            )
        print(
            "Flash save completion confirmed; power-cycle persistence not tested",
            flush=True,
        )
    print("Target configuration and active wire format verified", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--request-id", type=lambda value: int(value, 0), default=1)
    parser.add_argument("--side", choices=("left", "right"), required=True)
    parser.add_argument("--from-mode", choices=("can", "canfd"), required=True)
    parser.add_argument("--mode", choices=("can", "canfd"))
    parser.add_argument(
        "--save",
        action="store_true",
        help="Explicitly save the verified target to Flash",
    )
    parser.add_argument("--bitrate", type=int, default=1_000_000)
    parser.add_argument("--dbitrate", type=int, default=5_000_000)
    parser.add_argument("--timeout", type=float, default=0.5)
    args = parser.parse_args(argv)
    if not 0 <= args.request_id <= 0x3FE:
        parser.error("request ID must be within 0..0x3FE")
    if not 0 < args.bitrate <= 1_000_000 or not 0 < args.dbitrate <= 8_000_000:
        parser.error("invalid bitrates")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    if args.save and args.mode is None:
        parser.error("--save requires --mode")
    configure_host(args.channel, True, args.bitrate, args.dbitrate)
    with can.Bus(interface="socketcan", channel=args.channel, fd=True) as bus:
        if args.mode is None:
            inspect(
                bus, args.request_id, args.from_mode == "canfd", args.side, args.timeout
            )
        else:
            switch(
                bus,
                args.request_id,
                args.from_mode == "canfd",
                args.mode == "canfd",
                args.side,
                timeout=args.timeout,
                save=args.save,
            )
    if args.mode is not None:
        configure_host(args.channel, args.mode == "canfd", args.bitrate, args.dbitrate)


if __name__ == "__main__":
    main()
