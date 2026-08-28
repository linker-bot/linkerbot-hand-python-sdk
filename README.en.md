# linkerbot-hand

`linkerbot-hand` is the standalone Python SDK for the Linkerbot hand family.
L6, O6, L20Lite, L25, O20, L30, and O30i all use one `HandDevice` API.
Model differences are reported by `get_capabilities()`; model classes,
managers, `HandSession`, and the `linkerbot.hand` compatibility namespace are
not part of this package.

The complete Chinese API reference is under
[docs/zh/src/reference](./docs/zh/src/reference/README.md).

## Contract

- Constructing a `HandDevice` or transport performs no device I/O, starts no
  thread, and causes no motion.
- `open()`, `close()`, `start_polling()`, and `stop_polling()` are explicit and
  idempotent.
- Every `set_*()` method is non-blocking and returns a `CommandHandle`.
  Continuous targets use latest-wins scheduling.
- Plain `set_*()` and `get_*()` methods use exact protocol values without
  implicit physical-unit conversion.
- `set_percent_angles()` accepts `0..100`. Flexion axes consistently use
  `0=flexed/closed` and `100=extended/open`.
- Query-backed `get_*()` methods perform a fresh on-demand transaction.
  Getters declared as `COMMAND_CACHE` or `COMMAND_RESPONSE` return explicitly
  identified host cache state or a device write response. `get_snapshot()` and
  `get_health()` always read memory only.
- The SDK is the sole owner of periodic device queries. Applications and ROS
  submit a `PollingPlan` instead of creating another query timer.
- The core package does not depend on NumPy, SciPy, ROS, the Arm SDK, or the
  former aggregate SDK.

## Installation

Install from the Linkerbot Gitea Python package index:

```bash
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  linkerbot-hand
```

For source development:

```bash
python -m pip install -e .
```

Python 3.10 or newer is required. The only runtime dependency is
`linkerbot-transport` with its CAN and CAN FD backends.

## Quick Start

Configure classic SocketCAN at 1 Mbit/s before opening L6, O6, L20Lite, or L25:

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

Then use the single public device class:

```python
from linkerbot_hand import AngleFeedbackPolicy, HandDevice, PollingPlan
from linkerbot_transport.can import CANTransport

hand = HandDevice(
    model="o6",
    side="right",
    transport=CANTransport("can0"),
)

hand.open()
try:
    capabilities = hand.get_capabilities()
    print(capabilities.identity)
    print(capabilities.joint_names)

    hand.start_polling(
        PollingPlan(
            angles_hz=60,
            force_hz=30,
            temperature_hz=1,
            faults_hz=5,
            angle_feedback=AngleFeedbackPolicy.ADAPTIVE,
        )
    )

    handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])
    print(handle.command_id, handle.stage, handle.ack_support)

    angles = hand.get_angles(timeout=0.2)
    print(angles.raw)
    print(angles.percent)

    snapshot = hand.get_snapshot()
    health = hand.get_health()
    print(snapshot.angles)
    print(health.connection_state)
finally:
    hand.stop_polling()
    hand.close()
```

The default `ADAPTIVE` policy uses an angle-command response only when that
model profile verifies it as current state, and actively queries any missing
samples. Models without such a response fall back to active queries.
`QUERY_ONLY` always uses periodic active reads; `COMMAND_FEEDBACK_ONLY` is valid
only when the angle command capability declares `returns_current_state=True`.
Explicit `get_angles()` always remains an active read, and the SDK never
repeats the last motion target on its own.

`set_percent_angles()` causes motion. For an initial hardware check, use a
small target near the current pose after verifying the model, side, joint
order, and percentage endpoints.

L30 has no left/right variant, so its constructor must omit `side`:

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport

l30 = HandDevice(
    model="l30",
    transport=SocketCANFDTransport(
        "can0",
        bitrate=1_000_000,
        data_bitrate=5_000_000,
    ),
)
```

O20 uses the same 1/5 Mbit/s SocketCAN FD configuration. O30i uses 1 Mbit/s
for both nominal and data rates.

## Value Semantics

```python
# Here `hand` is the opened six-axis O6 device from the quick start.
raw_handle = hand.set_angles([255, 128, 255, 255, 255, 255])
percent_handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])

angles = hand.get_angles(timeout=0.2)
print(angles.raw)  # Exact device protocol values
print(angles.percent)  # Fixed SDK capability mapping
print(angles.physical_rad)  # None where no physical calibration exists
```

Configured speed, measured speed, current limit, measured current,
dimensionless effort limit, and true torque are separate channels. Check
`capabilities.command(...)` and `capabilities.telemetry_channel(...)` before
using an optional operation. Unsupported methods raise
`UnsupportedOperationError`.

## Command Delivery

A `CommandHandle` distinguishes:

- `ACCEPTED`: validation passed and the command entered a bounded scheduler.
- `DISPATCHED`: the transport successfully sent every frame.
- `ACKNOWLEDGED`: the device protocol returned an explicit matching ACK.
- `FAILED`: encoding, sending, protocol processing, or communication failed.
- `SUPERSEDED`: a newer target replaced the command before transport claim.

For devices without protocol ACK support, `DISPATCHED` is the successful
terminal stage. It never means that mechanical motion has completed.

## Verification

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest
ruff check src/linkerbot_hand tests/hand
ruff format --check src/linkerbot_hand tests/hand
ty check src/linkerbot_hand tests/hand/api_v1
python -m build
```

The repository contains only the Hand SDK. Arm control, kinematics, and
Pinocchio belong to the separate Arm SDK. ROS 2 messages, Lifecycle, QoS, and
topic mapping belong to `linkerbot-hand-ros`.
