# linkerbot-hand

Python SDK for Linkerbot hands: L6, O6, L20Lite, L20, O20, L30, and O30 share
`HandDevice`. Query model differences with `get_capabilities()`.

## Install from source

Install the current source of both hand and transport.
Place them in the same parent directory and run the following commands there to create a `py-linkerbot` environment.
The commands use the default clone directory names, `linkerbot-transport` and `linkerbot-hand-python`;
substitute your actual paths if your existing clones have different names.

```bash
uv venv py-linkerbot --python 3.12  # Create the virtual environment
source py-linkerbot/bin/activate  # Activate it in this terminal
uv pip install -e "./linkerbot-transport[all]"  # Install transport from source
uv pip install -e ./linkerbot-hand-python  # Install hand from source
```

See [pip, Windows, cloning, and installation checks](./docs/en/src/reference/quick-start.md).
For existing clones that still use the old repository URLs, see [Updating the SDK](./docs/en/src/reference/sdk-update.md).

## Discover and connect

```python
from linkerbot_hand import discover_hands
report = discover_hands()  # Scan the platform's default adapters
candidate = report.require_one()  # Require exactly one identified hand
hand = candidate.create_hand_device()  # Reuse the discovered connection settings
try:
    hand.open()  # Open and verify the device
    print(hand.get_angles(timeout=1.0).raw)  # Read protocol values; timeout is in seconds
finally:
    hand.close()  # Release the connection and background tasks
```

Discovery defaults depend on the platform and adapter. Drivers, correct bitrates, and interface setup are
still required. [Discovery](./docs/en/src/reference/connection/automatic.md) explains default enumeration,
multiple candidates, failures, and custom options. This example reads identity and angles only;
it sends no motion commands.

`set_angles()` and `set_speeds()` use protocol values; `set_percent_angles()` uses 0..100.
For continuous acquisition, combine `start_polling()` with `get_snapshot()`. Snapshots read
the SDK cache: also check validity, freshness, origin, and units.

## Documentation

[Full documentation index](./docs/en/src/reference/README.md)

| Task | Page |
| --- | --- |
| Install, connect, and run an example | [Quick start](./docs/en/src/reference/quick-start.md) |
| Run examples by model and function | [Seven-model tutorials](./docs/en/src/reference/README.md#models) |
| Read tactile data | See sensor reading in the [model tutorials](./docs/en/src/reference/README.md#models) |

ROS 2 messages, lifecycle, and topic mapping belong to the separate `linkerbot-hand-ros` repository.
