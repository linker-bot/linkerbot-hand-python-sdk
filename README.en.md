# linkerbot-hand

## Unified tactile fields (development branch)

`hand.get_force(timeout=1.0)` reads available `fn + fns` by default.
Use `hand.get_force("fn", timeout=1.0)` to select a quantity, then inspect
`sample.fn.raw`, `.units`, `.encodings`, `.valid_values` and `.meta` without I/O.
This API is not included in the published 2.0.0 installation examples below.

| Model | `fn` points/matrix | `fns` normal summary | `ft/fts/fd/fds/px` |
| --- | --- | --- | --- |
| L6 / O6 / L20 / L30 / O20 | Supported | Unsupported | Unsupported |
| L20Lite | Supported | Five raw summaries (`0x20`) | `fts/fds/px` via `0x21/22/23`; `ft/fd` unsupported |
| O30 | Descriptor-defined | Descriptor-defined | Descriptor-defined |

`PollingPlan(force_fn_hz=20)` uses the SDK scheduler. `force_hz` remains shorthand
for available fn+fns; combining it with positive `force_fn_hz`/`force_fns_hz` is
rejected. O30 reads its complete native stream for either selector and shares one
schedule at the maximum requested field rate. Other models query supported
categories independently. No implicit conversion or computed resultants.

`hand.get_capabilities().force_field("fn")` describes support and shared queries.
O30 support is `None` until descriptors are acquired. Explicit unsupported requests
raise; defaults skip known absent fields. Each cached field keeps its own timestamp.
See the [complete field contract](./docs/zh/src/reference/force.md).

`linkerbot-hand` is the standalone Python SDK for the Linkerbot hand family.
L6, O6, L20Lite, L20, O20, L30, and O30 all use one `HandDevice` API.
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
- `set_speeds()` accepts exact protocol values.
- `set_percent_angles()` accepts `0..100`. Flexion axes consistently use
  `0=flexed/closed` and `100=extended/open`.
- Query-backed `get_*()` methods perform a fresh on-demand transaction.
  Getters declared as `COMMAND_CACHE` or `COMMAND_RESPONSE` return explicitly
  identified host cache state or a device write response. `get_snapshot()` and
  `get_health()` always read memory only. Dedicated consumer threads may use
  `wait_for_snapshot()` to wait for a cache revision without device I/O.
- The SDK is the sole owner of periodic device queries. Applications and ROS
  submit a `PollingPlan` instead of creating another query timer.
- The core package does not depend on NumPy, SciPy, ROS, the Arm SDK, or the
  former aggregate SDK.

## Quick Start

The Hand SDK's only direct runtime dependency is `linkerbot-transport`; the
transport `[all]` extra installs `python-can` for the CAN and CAN FD backends.
Hand does not depend on ROS, an Arm SDK, kinematics, or an aggregate SDK. On
Ubuntu, the following commands create an isolated environment, install both
Linkerbot layers explicitly, and perform a hardware-free verification:

```bash
sudo apt update
sudo apt install -y python3-venv can-utils

python3 -m venv ~/venvs/linkerbot-hand-2.0.0
source ~/venvs/linkerbot-hand-2.0.0/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  'linkerbot-transport[all]==1.0.0' \
  'linkerbot-hand==2.0.0'

python - <<'PY'
from importlib.metadata import version

from linkerbot_hand import HandDevice, SUPPORTED_MODELS
from linkerbot_transport.can import CANTransport

print("linkerbot-transport", version("linkerbot-transport"))
print("linkerbot-hand", version("linkerbot-hand"))
print("models", ", ".join(SUPPORTED_MODELS))

transport = CANTransport("can0")
hand = HandDevice(model="l6", side="left", transport=transport)
print("lifecycle", hand.get_health().connection_state.value)
hand.close()
PY
```

The final four lines should show transport `1.0.0`, hand `2.0.0`, all seven supported
models, and `lifecycle closed`. Constructing the transport and `HandDevice`
does not open `can0`, start a thread, or perform device I/O, so no hardware is
required. In each new terminal, reactivate the environment first:

```bash
source ~/venvs/linkerbot-hand-2.0.0/bin/activate
```

Use an editable install only when developing unreleased source. Note that
`linkerbot-transport` is not on public PyPI, so **installing hand alone does not
work**: a bare `pip install -e .` fails because transport cannot be resolved.
Two workable forms.

Install both layers from source (use this when developing transport too):

```bash
git clone ssh://git@git.linkerhub.work:12222/zhuzhixiang/linkerbot-transport.git
git clone ssh://git@git.linkerhub.work:12222/zhuzhixiang/linkerbot-hand.git
python -m pip install -e './linkerbot-transport[all]' -e ./linkerbot-hand
```

Work on hand only and take a released transport (the common case):

```bash
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  -e .
```

Either form is verified the same way:

```bash
python -c 'import linkerbot_hand; print(linkerbot_hand.__file__)'
```

The path printed must be the local checkout, not `site-packages`.

Python 3.10 or newer is required. `linkerbot-hand` already declares its
transport dependency; listing both packages explicitly keeps the installed
versions and troubleshooting boundary visible to first-time users.

## First Device

Configure classic SocketCAN at 1 Mbit/s before opening L6, O6, L20Lite, or L20:

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

L30 reports its physical left/right side, but its control profile does not branch
by side, so its constructor must omit `side`:

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

O20 uses the same 1/5 Mbit/s SocketCAN FD configuration. O30 uses a 1 Mbit/s nominal rate with BRS off, so its data phase always equals the nominal rate and the interface `dbitrate` does not affect communication: **5 Mbit/s is recommended, but any data bitrate works** (1M, 2M, 7M and so on). Pass whatever the interface is actually configured with as `data_bitrate`.

## Explicit Discovery

When the channel, model, or side is unknown, identity discovery is an explicit,
read-only operation:

```python
from linkerbot_hand import discover_hands

report = discover_hands()
candidate = report.require_one()
hand = candidate.create_hand_device()
```

The example above is for one hand. For multiple hands on separate interfaces,
use named dictionaries directly, without calling `require_one()`:

```python
from linkerbot_hand import discover_hands

report = discover_hands()
named = report.named_candidates()  # e.g. l6l, l6r, o6l, o6r
hands = report.create_hand_devices()  # VERIFIED candidates only; no device I/O
```

Multiple hands of the same model and side become `l6l1`, `l6l2`, etc. L30 uses
its verified reported side for `l30l` / `l30r`. These are scan-local aliases,
not persistent identities; adding or removing devices may change them. No verified
candidates returns an empty dictionary. Check partial identities, failures and
probe cleanup in the report before opening devices; see the discovery reference.

Candidate factories retain any recorded identity fingerprint. The first `open()`
and retries must match the discovery identity: replacing a discovered left L30
with a right L30 on the same channel is rejected. Rediscover and construct a new
device after replacement or changes to fingerprinted version fields, including
O30 firmware. See the discovery reference for identity-binding limits.

The default source combines SocketCAN and MiniCANFD on Linux, or PCAN, SLCAN,
and MiniCANFD on Windows. SocketCAN links must already be up; discovery never
changes their state or bitrates. USB backends require their drivers, and Linux
MiniCANFD requires an explicitly confirmed channel layout. PCAN defaults to
classic CAN; SLCAN and MiniCANFD default to CAN FD. See the
[discovery reference](./docs/zh/src/reference/hand-discovery.md) for configuration
and the required transport source version.

Discovery reads identity only; it never starts polling or sends motion,
fault-clear, calibration, or hand configuration commands. SocketCAN FD links
are probed with classic frames first. A fully verified identity ends probing
on that channel; a partial reply does not suppress the next protocol. FD probes
use the O20, O30, then L30 identity protocols. A backend failure is reported
without discarding results from other backends.

L6, O6, L20Lite, and L20 first query `0xFF#C0`, falling back to `0x27` and
then `0x28` if the product reply is absent or incomplete. Both fragment indices
and byte offsets are accepted. Replies retain their actual CAN ID; non-default
classic addresses are reported as partial candidates and cannot create a device.

The maintained setup is one hand per channel; use separate interfaces for multiple
hands. Multiple candidates, observable same-address collisions, malformed identity,
and missing required fields are never silently resolved. O30 identity reports the
physical side, so its candidate already carries a resolved `side` and needs no
explicit one:

```python
candidate = report.require_one()
hand = candidate.create_hand_device()
```

Do not hard-code the side: passing one that disagrees with the discovered identity
raises `ValidationError`. Restrict discovery to the intended interface and use
`require_one()` to check that it has exactly one candidate before creating a device.

Discovery closes probe transports before returning; a failed close is reported
with a `failure.cleanup()` callback to retry before reopening that channel.
The candidate retains the full connection recipe, and the returned `HandDevice`
remains closed until `open()` is called. `DiscoveryConfig.total_timeout_s`
(default 30 seconds) bounds protocol waits and admission of further probes;
blocking native driver calls and required cleanup can exceed that budget.

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

Configured speed, measured speed, current limits, measured current, torque limits,
and torque data are separate channels. Use `get_currents()` and
`set_current_limits()` for current. L20 has no limit-configuration getter;
L20Lite exposes `get_current_limits()` and `get_configured_speeds()` as
`COMMAND_RESPONSE` caches of complete write echoes from this connection, without
sending queries. Its setting commands finish as `ACKNOWLEDGED` only after both
groups reply; live current/speed queries remain separate. O30 also provides independent
`get_torques()` and `set/get_torques_limits()` methods. Values remain protocol raw;
units and scale notes come from capabilities, without implicit conversion. Check
`capabilities.command(...)` and `capabilities.telemetry_channel(...)` before
using an optional operation. Unsupported methods raise
`UnsupportedOperationError`.

L6 V1.0.2 also supports `set_torques()` / `get_torques()` for raw `0x03` torque
output and mode values, plus `set/get_position_kp()`, `set/get_position_kd()` and
`set/get_torque_kd()`. Each zero selects position mode; 1..255 selects torque mode
and its output. Physical torque measurement and scale are unverified, so the
telemetry capability keeps `device_measured=False`. `get_currents()` remains the
independent `0x36` measured-current channel, and `set/get_current_limits()` uses
`0x02` current-limit settings. See the [L6 reference](./docs/zh/src/reference/models/l6.md) for
gain scales, mode-switch timing and device response differences. Other models
do not advertise these new commands.

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

Release history is maintained in [CHANGELOG.md](CHANGELOG.md). Every pull
request that changes user-facing behavior, APIs, dependencies, or
documentation must update its `Unreleased` section.
