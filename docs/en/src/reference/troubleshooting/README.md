# Troubleshooting

## Import Failures

Make sure you use the same Python environment to install the SDK and run your program.

```bash
# Show the current Python path
python -c "import sys; print(sys.executable)"
# Show the SDK import path
python -c "import linkerbot_hand; print(linkerbot_hand.__file__)"
```

If a module is missing, activate the environment used for installation and install dependencies according to the [installation instructions](../quick-start.md).

## No Devices Found

Check the interface name, bus type, and bit rates, and close other programs using the same channel. Linux SocketCAN requires you to [configure and enable the interface](../connection/prepare.md) first.

For manual connections, check the model and hand side. For automatic connections, inspect error codes and messages in `report.failures`:

```python
# report is the scan result returned by discover_hands()
for failure in report.failures:
    print(failure.code, failure.message)
```

Install the MiniCANFD driver and vendor library. Linux checks channel `0` first, then `1` if no candidate is found and cleanup succeeds. `no_hand_found` means the checks finished without finding a hand. Handle missing libraries, busy channels, and cleanup failures according to their diagnostics; see [Automatic Connection](../connection/automatic.md#minicanfd).

Windows SLCAN auto enumeration selects only `16D0:117E`; specify ports for other USB identifiers. Check CAN FD firmware and serial baud rate, which defaults to `115200`. For Linux CAN / CAN FD, confirm that the driver or firmware exposes the appropriate SocketCAN interface.

If multiple devices are found, use the multiple-device example on the Automatic Connection page. The single-device example requires exactly one discovered device.

## Connection or Read Timeouts

Check the interface, model, and hand side, and close other programs using the same channel. See [Manual Connection](../connection/manual.md) for connection settings.

The `timeout` parameter in read methods is measured in seconds. You may increase it for a slow response; persistent timeouts indicate that communication settings should be checked.

## Control Failures

Make sure `hand.open()` has been called. Check the control mode, parameter ranges, and value order against the [feature guide for your model](../README.md#models).

Sending a command does not mean the device has reached its target position. Use the corresponding read method to verify device status. Use the appropriate setter for raw angles or angle percentages.

[Back to overview](../README.md)
