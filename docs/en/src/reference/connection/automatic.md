# Automatic Connection

<a id="automatic"></a>

The SDK scans channels and identifies device models and hand sides. For Linux SocketCAN, [prepare the communication interface](./prepare.md) first.

Choose the single-device or multiple-device example below.

`discover_hands()` scans SocketCAN and MiniCANFD by default on Linux, and PCAN, SLCAN, and MiniCANFD on Windows. Use SocketCAN for Linux CAN / CAN FD interfaces; the driver or firmware must expose and enable the interface first.

PCAN identifies Classical CAN hands by default; SLCAN and MiniCANFD identify CAN FD hands. Scanning reads identity and sends no motion commands. Use a separate channel for each hand, with one hand per MiniCANFD adapter.

## Connecting One Device

The program raises an error if no device or more than one device is found.

```python
from linkerbot_hand import discover_hands
# Scan and identify devices
report = discover_hands()
hand = report.require_one().create_hand_device()
try:
    # Open the device connection
    hand.open()
    # Read device identity information
    print(hand.get_device_info(timeout=1.0))
finally:
    # Close the connection and release resources
    hand.close()
```

## Connecting Multiple Devices

This example connects to each discovered device in sequence and prints identity information only for L6 devices.

```python
from linkerbot_hand import discover_hands
# Scan devices; use the configuration below when adapter parameters are required
report = discover_hands()
model = "L6"  # Change this to the model whose identity should be printed
# Stop if no device is identified
if not report.verified_candidates:
    raise RuntimeError("No device found")
# Create each connection from the discovered model and hand side
for candidate in report.verified_candidates:
    # Create the device object from discovery results
    hand = candidate.create_hand_device()
    try:
        # Open the device connection
        hand.open()
        # Connect every device, but print identity only for the selected model
        if candidate.model == model:
            # Read device identity information
            print(hand.get_device_info(timeout=1.0))
    finally:
        # Close the connection and release resources
        hand.close()  # Close this connection before opening the next one
```

Check `report.failures` if no device is found, identity is incomplete, or connection cleanup fails; see [Troubleshooting](../troubleshooting/README.md). `require_one()` raises an error for no devices, multiple candidates, or incomplete identity.

## Adapter Parameters

To scan only one adapter type, replace `report = discover_hands()` with the corresponding call below.

### PCAN (Windows)

After installing the vendor driver and PCANBasic library, the SDK enumerates available PCAN channels without a channel count.

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# Scan available PCAN channels for Classical CAN hands
report = discover_hands(DiscoveryConfig(backends=("pcan",)))
```

<a id="minicanfd"></a>

### MiniCANFD

After installing the vendor driver and library, the SDK detects the adapter count automatically. Device indices and `linux_channels` are optional. Linux uses `libcanbus.so`; Windows uses `HCanbus.dll`. The library must match Python's architecture and be discoverable by the system.

On Linux, each adapter starts at channel `0`. If no identity candidate is found and the connection closes successfully, the SDK checks channel `1` on the same adapter. If channel `0` is not FD, it checks `1` directly; a candidate on `0` stops the fallback. If both checks finish without a candidate, the report includes `no_hand_found`. Missing libraries, busy channels, and cleanup failures keep their own diagnostics. Legacy device-level libraries support channel `0` only.

Windows scans available FD channels enumerated by the vendor library. To scan MiniCANFD only:

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# Scan and identify devices
report = discover_hands(
    DiscoveryConfig(
        # Scan only the selected backend
        backends=("minicanfd",),
    )
)
```

If the library cannot be found, supply its actual path with `MiniCANFDDiscoveryConfig(library_path=...)`:

```python
from linkerbot_hand import DiscoveryConfig, MiniCANFDDiscoveryConfig, discover_hands
# Linux example; on Windows use a path such as r"C:\MiniCANFD\HCanbus.dll"
report = discover_hands(
    DiscoveryConfig(
        backends=("minicanfd",),
        minicanfd=MiniCANFDDiscoveryConfig(library_path="/opt/minicanfd/libcanbus.so"),
    )
)
```

Explicit `linux_channels` or global `DiscoveryConfig.channels` restrict scanning to the selected channels and disable automatic fallback from `0` to `1`. See [Manual Connection](./manual.md#minicanfd) for channel indices.

<a id="slcan"></a>

### SLCAN (Windows)

The SDK automatically enumerates CANable 2.0 serial ports with USB VID/PID `16D0:117E`; no port names or device count are required. This identifies the adapter type, not an individual unit, and does not prove CAN FD firmware support. The adapter must run firmware supporting the CANable 2.0 CAN FD extension.

Serial baud rate defaults to `115200` and is separate from CAN bitrates. Use the default configuration as follows:

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# Enumerate serial ports matching the USB identifiers
report = discover_hands(DiscoveryConfig(backends=("slcan",)))
```

For other USB identifiers or a specific port selection, pass `channels`. List several ports, such as `("COM5", "COM6")`, to scan several adapters. Set `tty_baudrate` in `bus_options` only when the adapter requires another serial baud rate:

```python
from linkerbot_hand import DiscoveryConfig, PythonCANDiscoveryConfig, discover_hands
# Scan and identify devices
report = discover_hands(
    DiscoveryConfig(
        # Scan only the selected backend
        backends=("slcan",),
        slcan=PythonCANDiscoveryConfig(
            # Use the actual serial port
            channels=("COM5",),  # For example: ("COM5", "COM6")
        ),
    )
)
```

After connecting, call the [features for your model](../README.md#models) between `hand.open()` and `hand.close()`.

[Back to overview](../README.md)
