"""O30 HOP over SocketCAN classic CAN: read, poll, optionally write and move.

Run: python examples/o30_classic_can.py --channel can0 --side right
The adapter and hand must already use classic CAN at the same nominal bitrate.
This script does not change CAN mode, bitrate, CAN ID or flash settings.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

import can

from linkerbot_hand import (
    CommandStage,
    DeviceConfig,
    HandDevice,
    PollingPlan,
    TransportKind,
    UnsupportedFirmwareError,
)
from linkerbot_hand.protocols.hop.framing import build_read_message
from linkerbot_hand.protocols.hop.response import HOPRangeAssembler
from linkerbot_hand.protocols.o30.codec import decode_v25
from linkerbot_transport.can import CANTransport

READS = (
    "angles",
    "speeds",
    "configured_speeds",
    "configured_accelerations",
    "currents",
    "current_limits",
    "torques",
    "torques_limits",
    "temperatures",
    "voltages",
    "motion_times",
    "stall_detection_times",
    "stall_thresholds",
    "stall_hold_currents",
    "faults",
)
WRITE_BACK = (
    ("configured_speeds", "set_speeds"),
    ("configured_accelerations", "set_accelerations"),
    ("current_limits", "set_current_limits"),
    ("torques_limits", "set_torques_limits"),
    ("motion_times", "set_motion_times"),
    ("stall_detection_times", "set_stall_detection_times"),
    ("stall_thresholds", "set_stall_thresholds"),
    ("stall_hold_currents", "set_stall_hold_currents"),
)


def encode(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: encode(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def checked(command):
    stage = command.wait(timeout=3)
    if stage is not CommandStage.ACKNOWLEDGED:
        raise RuntimeError(f"command {stage.value}: {command.error}")
    return {"stage": stage.value}


def read_targets(channel, request_id, timeout=1.0):
    """Read O30 RTS=1 targets for this script, while SDK polling is stopped.

    This isolated validation helper reuses the SDK codec. Do not run another
    query or command producer on the same endpoint during the exercise.
    """
    assembler = HOPRangeAssembler(
        1, sub_index=0, length=25, rts=True, response_id=request_id | 0x400
    )
    with can.Bus(interface="socketcan", channel=channel) as bus:
        bus.set_filters(
            [{"can_id": request_id | 0x400, "can_mask": 0x7FF, "extended": False}]
        )
        deadline = time.monotonic() + timeout
        for start in range(0, 25, 5):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            bus.send(
                build_read_message(
                    1,
                    sub_index=start,
                    length=5,
                    request_id=request_id,
                    rts=True,
                    can_fd=False,
                ),
                timeout=remaining,
            )
            current = HOPRangeAssembler(
                1, sub_index=start, length=5, rts=True, response_id=request_id | 0x400
            )
            chunk = None
            while (remaining := deadline - time.monotonic()) > 0:
                message = bus.recv(remaining)
                if message is None:
                    break
                matched, chunk = current.consume(message)
                if matched:
                    _, complete = assembler.consume(message)
                    if complete is not None:
                        return decode_v25(complete)
                if chunk is not None:
                    break
            if chunk is None:
                break
    raise TimeoutError("O30 target register read did not complete")


def observe_angles(hand, duration, trace):
    """Observe fresh feedback for a time window without assuming arrival."""
    deadline = time.monotonic() + duration
    latest = None
    while latest is None or time.monotonic() < deadline:
        # A request gets its own response budget even near the window boundary.
        latest = hand.get_angles(timeout=1.0).raw
        trace.append(latest)
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    return latest


def exercise_motion(hand, args, report, target_reader):
    joint = args.motion_joint
    original_targets = tuple(target_reader())
    initial = hand.get_angles(timeout=3).raw
    target = list(original_targets)
    target[joint] += args.delta
    motion = report["motion"] = {
        "original_targets": original_targets,
        "initial_actual": initial,
        "target": target,
        "joint": joint,
        "observation_duration_s": args.motion_timeout,
        "observations": [],
        "restore_observations": [],
    }
    if not 0 <= target[joint] <= 255:
        raise ValueError("motion target is outside 0..255; choose another delta")
    try:
        motion["command"] = checked(hand.set_angles(target))
        motion["target_readback"] = tuple(target_reader())
        if motion["target_readback"] != tuple(target):
            raise RuntimeError("motion target register readback mismatch")
        motion["observed"] = observe_angles(
            hand, args.motion_timeout, motion["observations"]
        )
    except Exception as error:
        motion["command_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # Keep restoration errors in the report even when an earlier step fails.
        try:
            motion["restore"] = checked(hand.set_angles(original_targets))
            motion["restored_targets"] = tuple(target_reader())
            if motion["restored_targets"] != original_targets:
                raise RuntimeError("original target register restoration failed")
            motion["restored_observed"] = observe_angles(
                hand, args.motion_timeout, motion["restore_observations"]
            )
        except Exception as error:
            motion["restore_error"] = f"{type(error).__name__}: {error}"
            raise
    motion["signed_motion_raw"] = (motion["observed"][joint] - initial[joint]) * (
        1 if args.delta > 0 else -1
    )
    motion["target_error_raw"] = abs(motion["observed"][joint] - target[joint])
    motion["restore_error_raw"] = abs(
        motion["restored_observed"][joint] - initial[joint]
    )
    motion["restored_target_error_raw"] = abs(
        motion["restored_observed"][joint] - original_targets[joint]
    )
    motion["actual_motion_observed"] = any(
        sample[joint] != initial[joint] for sample in motion["observations"]
    )
    motion["other_joint_max_change_raw"] = max(
        abs(value - before)
        for sample in motion["observations"] + motion["restore_observations"]
        for index, (value, before) in enumerate(zip(sample, initial, strict=True))
        if index != joint
    )
    motion["status"] = "TARGETS_VERIFIED"
    print("motion: targets verified; actual feedback is recorded separately")


def exercise(hand, args, report, *, target_reader=None):
    """Exercise an opened device; ordinary read/write errors fail the run."""
    report["identity"] = encode(hand.get_device_info(timeout=3))
    report["reads"] = {}
    for name in READS:
        sample = getattr(hand, "get_" + name)(timeout=3)
        report["reads"][name] = encode(sample)
        print(f"{name}: {sample.raw}")
    try:
        report["force"] = encode(hand.get_force("fn", timeout=5))
        print("force: received (see report for supported fields and validity)")
    except UnsupportedFirmwareError as error:
        report["force"] = {"status": "SKIP", "reason": str(error)}
        print(f"force: SKIP ({error})")

    if args.write_back:
        report["write_back"] = {}
        for name, setter in WRITE_BACK:
            before = report["reads"][name]["raw"]
            entry = report["write_back"][name] = checked(getattr(hand, setter)(before))
            after = getattr(hand, "get_" + name)(timeout=3).raw
            entry["readback"] = encode(after)
            if list(after) != before:
                raise RuntimeError(
                    f"{name}: readback differs from original configuration"
                )
        print("write-back: configuration readback matched")

    if args.motion_joint is not None:
        exercise_motion(
            hand,
            args,
            report,
            target_reader or (lambda: read_targets(args.channel, args.request_id)),
        )

    start_sequence = hand.get_snapshot().angles.meta.sequence
    sequences = set()
    hand.start_polling(PollingPlan(angles_hz=args.angles_hz, faults_hz=1))
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.duration:
            sample = hand.get_snapshot().angles
            if sample.meta.valid and sample.meta.sequence > start_sequence:
                sequences.add(sample.meta.sequence)
            time.sleep(0.002)
    finally:
        hand.stop_polling()
    report["polling"] = {
        "requested_angles_hz": args.angles_hz,
        "duration_s": time.monotonic() - start,
        "observed_unique_angle_samples": len(sequences),
        "latest": encode(hand.get_snapshot().angles),
    }
    if not sequences:
        raise RuntimeError("polling produced no new angle sample")
    report["health"] = encode(hand.get_health())
    print("polling:", len(sequences), "distinct angle samples observed")
    health = report["health"]
    recent = health["recent_error"]
    expected_sensor_skip = (
        report["force"].get("status") == "SKIP"
        and recent is not None
        and recent["code"] == "unsupported_firmware"
        and recent["operation"] == "get_force"
    )
    if (
        not health["connected"]
        or health["consecutive_errors"]
        or (recent is not None and not expected_sensor_skip)
    ):
        raise RuntimeError("polling reported a communication error; see report health")


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="can0")
    parser.add_argument("--side", choices=("left", "right"), required=True)
    parser.add_argument(
        "--request-id",
        type=lambda v: int(v, 0),
        help="actual request ID; defaults to right=1, left=2",
    )
    parser.add_argument("--duration", type=positive, default=3)
    parser.add_argument("--angles-hz", type=positive, default=20)
    parser.add_argument(
        "--write-back",
        action="store_true",
        help="write existing values and verify configuration readback",
    )
    parser.add_argument(
        "--motion-joint",
        type=int,
        choices=range(20),
        help="change one joint target (0-based index), then restore original targets",
    )
    parser.add_argument(
        "--delta", type=int, default=2, help="raw angle change, -5..5 excluding zero"
    )
    parser.add_argument(
        "--motion-timeout",
        type=positive,
        default=2,
        help="seconds of feedback observations after each target write",
    )
    parser.add_argument(
        "--report", type=Path, default=Path("o30_classic_can_report.json")
    )
    args = parser.parse_args()
    if args.delta == 0 or not -5 <= args.delta <= 5:
        parser.error("--delta must be -5..5 excluding zero")
    if args.request_id is None:
        args.request_id = 1 if args.side == "right" else 2
    if not 0 <= args.request_id <= 0x3FE:
        parser.error("--request-id must be 0..0x3FE")
    frames = []
    report = {"arguments": vars(args) | {"report": str(args.report)}, "frames": frames}
    hand = None
    exit_code = 1

    def record(message):
        frames.append(
            {
                "timestamp": message.timestamp,
                "id": message.arbitration_id,
                "data": bytes(message.data).hex(),
                "is_fd": message.is_fd,
                "brs": message.bitrate_switch,
                "error": message.is_error_frame,
                "extended": message.is_extended_id,
            }
        )

    try:
        with can.Bus(interface="socketcan", channel=args.channel) as observer:
            notifier = can.Notifier(observer, [record], timeout=0.1)
            try:
                hand = HandDevice(
                    model="O30",
                    side=args.side,
                    transport=CANTransport(args.channel),
                    config=DeviceConfig(
                        hop_request_id=args.request_id, hop_transport=TransportKind.CAN
                    ),
                )
                hand.open()
                exercise(hand, args, report)
                hand.close()
                hand.open()
                report["reopen_angles"] = hand.get_angles(timeout=3).raw
                hand.recover(timeout=5)
                report["recovered_angles"] = hand.get_angles(timeout=3).raw
                endpoint_frames = [
                    f
                    for f in frames
                    if f["id"] in (args.request_id, args.request_id | 0x400)
                ]
                if not endpoint_frames or any(
                    f["is_fd"]
                    or f["brs"]
                    or f["error"]
                    or len(bytes.fromhex(f["data"])) > 8
                    for f in endpoint_frames
                ):
                    raise RuntimeError(
                        "expected classic CAN endpoint traffic without errors"
                    )
                report["status"] = "PASS"
                exit_code = 0
            finally:
                try:
                    if hand is not None:
                        hand.close()
                finally:
                    notifier.stop()
    except Exception as error:  # noqa: BLE001 - CLI saves failures with captured frames
        report["status"] = "FAIL"
        report["error"] = f"{type(error).__name__}: {error}"
        print(report["error"])
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(encode(report), ensure_ascii=False, indent=2) + "\n"
        )
        print("report:", args.report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
