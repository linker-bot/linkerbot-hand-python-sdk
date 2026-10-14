# Manual Connection

Choose the connection example for your adapter, specify the channel, model, and hand side, and call `hand.open()` to connect. L30 does not require a hand side.

For Linux SocketCAN, [prepare the communication interface](./prepare.md) first.

<a id="socketcan"></a>

## SocketCAN（CAN / CAN FD）

[Prepare the communication interface](./prepare.md) first and replace `can0` with your actual interface name. In the examples, `model` and `side` specify the model and hand side. L30 does not require `side`.

### Classical CAN

<a id="can-classic"></a>

This example connects to a right O6 hand and also applies to L6, L20Lite, and L20.

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport
# Create the device object
hand = HandDevice(
    model="O6",  # Device model
    side="right",  # Hand side: left or right; omit for L30
    transport=CANTransport("can0"),
)
try:
    # Open the device connection
    hand.open()
    # Read current raw angles
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # Close the connection and release resources
    hand.close()
```

### CAN FD

This example connects to a right O30 hand and also applies to O20 and L30.

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport
# Create the device object
hand = HandDevice(
    model="O30",  # Device model
    side="right",  # Hand side: left or right; omit for L30
    transport=SocketCANFDTransport("can0"),
)
try:
    # Open the device connection
    hand.open()
    # Read current raw angles
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # Close the connection and release resources
    hand.close()
```

<a id="pcan-slcan"></a>

## PCAN and SLCAN (Windows)

Replace the example channel, model, and hand side with your actual settings. L30 does not require `side`.

### PCAN Classical CAN

<a id="pcan"></a>

This Windows example connects to a right O6 hand through `PCAN_USBBUS1`; replace it with your actual PCAN channel name. Install the vendor driver and PCANBasic library, matching Python's architecture (32-bit or 64-bit). On Linux, use the SocketCAN examples above once the driver exposes an interface such as `can0`.

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport
# Create the device object
hand = HandDevice(
    model="O6",  # Device model
    side="right",  # Hand side: left or right; omit for L30
    transport=CANTransport("PCAN_USBBUS1", "pcan", bitrate=1_000_000),
)
try:
    # Open the device connection
    hand.open()
    # Read current raw angles
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # Close the connection and release resources
    hand.close()
```

### SLCAN CAN FD

<a id="slcan"></a>

This Windows example connects to a right O30 hand through `COM5`; replace it with your actual serial port. The adapter must run firmware supporting the CANable 2.0 CAN FD extension. Serial baud rate defaults to `115200`; set `tty_baudrate` in `bus_options` only to override it. On Linux, use SocketCAN CAN FD with a driver, firmware, and interface that support CAN FD.

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import PythonCANFDTransport
# Create the device object
hand = HandDevice(
    model="O30",  # Device model
    side="right",  # Hand side: left or right; omit for L30
    transport=PythonCANFDTransport(
        "COM5",
        "slcan",
        bitrate=1_000_000,  # Arbitration bitrate in bit/s
        data_bitrate=5_000_000,  # Data bitrate in bit/s
    ),
)
try:
    # Open the device connection
    hand.open()
    # Read current raw angles
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # Close the connection and release resources
    hand.close()
```

<a id="minicanfd"></a>

## MiniCANFD

MiniCANFD supports Linux and Windows. This example connects to a right O30 hand. Adjust the channel index, model, and hand side for your device. L30 does not require `side`.

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import CANFDConfigOptions, CANFDTransport
# Create the device object
hand = HandDevice(
    model="O30",  # Device model
    side="right",  # Hand side: left or right; omit for L30
    transport=CANFDTransport(
        # Channel index on Windows; device index on Linux
        device_index=0,
        # Always 0 on Windows; channel index on Linux
        channel_index=0,
        # Set arbitration and data bitrates in bit/s
        config=CANFDConfigOptions(nom_baud=1_000_000, dat_baud=5_000_000),
    ),
)
try:
    # Open the device connection
    hand.open()
    # Read current raw angles
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # Close the connection and release resources
    hand.close()
```

> **Platform difference:** On Windows, `device_index` is the channel index and `channel_index` must be `0`. On Linux, `device_index` and `channel_index` are the device and channel indices, respectively.

For three Linux adapters with hands on channel `0`, use `(device_index, channel_index)=(0, 0)`, `(1, 0)`, and `(2, 0)`. For a hand on channel `1`, set `channel_index=1`. Manual connections do not try other channels; use [Automatic Connection](./automatic.md#minicanfd) to detect the adapter count and enable fallback. Pass `library_path` to `CANFDTransport` if the vendor library cannot be found.

After connecting, call the [features for your model](../README.md#models) between `hand.open()` and `hand.close()`. Call `hand.close()` when finished to release the connection.

Keep `hand.open()` inside `try` so `finally` also calls `hand.close()` after a failed
open. If cleanup fails, keep the same `HandDevice` and retry `hand.close()` after
resolving the driver or connection error, before reopening it. Cleanup retries do
not reopen the channel or close a connection still leased by another device.

[Back to overview](../README.md)
