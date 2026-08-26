# linkerbot-hand

[简体中文](README.md) | English

`linkerbot-hand` is the independently installable SDK for Linkerbot dexterous
hands. It contains the common `HandDevice` / `HandSession` contract, capability
data, command scheduling, cached snapshots, health reporting, deterministic
test doubles, and the bundled L6, O6, L20Lite, L25, L30, O20, and O30i hardware
backends.

The wheel has no ROS, arm, kinematics, Pydantic, SciPy, or aggregate SDK
dependency. It uses `linkerbot-transport` for CAN/CAN FD communication and can
be installed directly for hand-only applications:

```bash
pip install linkerbot-hand==0.2.0
```

### NumPy compatibility

The SDK uses NumPy for tactile matrices but does not depend on SciPy. Supported
ranges are selected by Python version:

- Python 3.10-3.12: `numpy>=1.21.6,<2`.
- Python 3.13 and newer: `numpy>=2.1,<3`.

ROS 2 Jazzy uses Python 3.12 and therefore resolves NumPy 1.26.x, which remains
compatible with Ubuntu's SciPy 1.11.4 (`numpy<1.28`). Release 0.1.1 incorrectly
required NumPy 2.2.6 or newer and should not be used in a Jazzy
`--system-site-packages` environment.

The canonical unified API is `linkerbot_hand`. Model drivers remain available
under `linkerbot.hand`, but this release unifies their value semantics: default
control and telemetry use protocol-native values. User LUT behavior and the old
raw-angle aliases are not retained.

## Lifecycle

Construction performs no device I/O and starts no polling or motion. A backend
is created only by `open()`, and polling starts only after an explicit
`start_polling()` call.

```python
from linkerbot_hand import (
    CommandPolicy,
    HandDevice,
    HandSide,
    PollingPlan,
    SessionConfig,
    get_model_capabilities,
)
from linkerbot_hand.testing import FakeBackendFactory

capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
backend_factory = FakeBackendFactory(capabilities)
hand = HandDevice(
    capabilities,
    backend_factory,
    config=SessionConfig(command_rate_hz=100, auto_reconnect=False),
)

hand.open()
try:
    hand.start_polling(PollingPlan(angles_hz=60, force_hz=30, temperature_hz=1))
    handle = hand.submit_percentage_angles(
        [100, 100, 100, 100, 100, 100],
        policy=CommandPolicy.LATEST,
    )
    snapshot = hand.get_snapshot()  # Cached data only; never device I/O.
    health = hand.get_health()  # Cached counters only.
finally:
    hand.stop_polling()
    hand.close()
```

`open()`, `close()`, `start_polling()` with the same plan, and
`stop_polling()` are idempotent. Closing waits for the worker for the bounded
`SessionConfig.join_timeout_s` interval.

## Commands

`submit_angles()`, `submit_speeds()`, and `submit_torques()` accept values in
the ranges and protocol units declared by capabilities. Explicit normalized
control is available through `submit_percentage_angles()` and, only for models
that declare it, `submit_percentage_speeds()`. These methods validate and
enqueue work without waiting for device I/O.
Continuous motion commands share one latest-wins slot. Discrete operations
(`clear_faults()`, `enable()`, `disable()`, and `calibrate()`) use a separate,
bounded FIFO when the model advertises support.

Every `CommandHandle` records these stages:

- `ACCEPTED`: validated and admitted to the scheduler.
- `DISPATCHED`: the backend reports a successful transport send.
- `ACKNOWLEDGED`: the device protocol explicitly confirms the command.
- `FAILED`: validation has already passed, but dispatch or communication fails.
- `SUPERSEDED`: a newer continuous target replaces this target before dispatch.

Check `handle.ack_support`. For a command whose protocol has no device ACK,
`DISPATCHED` is terminal and is never relabeled as `ACKNOWLEDGED`.

## Cached State And Capabilities

`get_snapshot()` only copies SDK cache state. Each `SensorSample` includes its
decoded protocol values, unit, wire encoding, collection timestamp, sequence,
validity, staleness, and connection generation. `get_health()` reports connection state, successful
send/receive times, errors, reconnects, superseded and pending command counts,
and sensor ages.

`get_capabilities()` is the runtime source of truth for model, firmware, side,
joint order and ranges, sensors, recommended polling rates, transport, optional
operations, command-specific ACK support, and explicit percentage mappings.
The default submit and snapshot paths are protocol-native and never apply
percentage, RPM, or milliamp conversion. Percentage angles use
`100=open, 0=closed` for flexion axes; directional endpoints come from
capabilities in the unified `submit_percentage_angles()` API. Model-manager
`set_percentage_angles()` helpers retain their explicit local mappings: the
four classic models map `0..100` to `0..255`, O20 and L30 map each joint's
minimum to maximum, and O30i uses its measured inverse-byte mapping. Those
manager percentages can differ from unified capability direction on some axes;
protocol values are portable across both entry points. Physical-angle
conversion raises `UnsupportedOperationError` unless a calibrated physical
range is explicitly provided.

## Recovery And Testing

`recover()` replaces and re-verifies the backend, clears pre-recovery commands,
and samples current state before publishing a new connection generation. It
does not replay old targets, move the hand, or clear faults. Automatic reconnect
is opt-in through `SessionConfig(auto_reconnect=True)`.

`linkerbot_hand.testing` provides `FakeBackend`, `FakeBackendFactory`,
`FakeBackendState`, and `ManualClock` for deterministic disconnect, timeout,
polling, queue, and recovery tests without hardware.

Custom `HandBackend.dispatch()` implementations must call `claim_dispatch()`
immediately before their first transport send. A rejected claim returns without
I/O. After a successful claim, call `mark_dispatched()` only once an actual
transport send succeeds; enqueueing into another software queue is not a send.

## Existing Hardware Drivers

The bundled hardware drivers remain available from their historic
`linkerbot.hand` paths. Construct the unified interface through the compatibility
adapter:

```python
from linkerbot.hand import create_hand_device

hand = create_hand_device("L6", side="right", interface_name="can0")
```

The legacy `linkerbot.hand` import path also re-exports `HandDevice`,
`HandSession`, `PollingPlan`, and `CommandPolicy` during migration.

Model-specific applications can keep the same import paths:

```python
from linkerbot.hand import L6, L30
```

Their default `set_angles()`, `set_speeds()`, and `set_torques()` methods and
sensor data now use protocol-native values. Percentage control is explicitly
named `set_percentage_*`. LUT configuration, `set_raw_angles()`, and
`set_hardware_raw_angles()` have been removed without a compatibility layer.

The independent wheel does not own the shared `linkerbot` package root, so
aggregate-only imports such as `from linkerbot import L6` should use the
equivalent `linkerbot.hand` import above.

## API Reference

See the [`linkerbot_hand` API reference
(Chinese)](docs/zh/src/reference/hand-device.md) for complete signatures,
return types, lifecycle semantics, command stages, telemetry, and stable errors.
