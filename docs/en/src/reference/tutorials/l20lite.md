# L20Lite Feature Guide

L20Lite uses Classical CAN and has 10 joints. In the examples below, `hand` is an already connected device object.

## Contents

- [Joint Order](#joint-order)
- [Device Identity](#identity)
- [Device Capabilities](#capabilities)
- [Angle Control](#angle-control)
- [Speed Control](#speed-control)
- [Setting Acceleration](#acceleration)
- [Reading Sensor Data](#sensor-reading)

## Joint Order

The SDK reads and sets joint data in the following order.

| Position | `joint_names` | Joint |
| ---: | --- | --- |
| 1 | `thumb_flex` | Thumb flexion |
| 2 | `thumb_roll` | Thumb roll |
| 3 | `index_flex` | Index finger flexion |
| 4 | `middle_flex` | Middle finger flexion |
| 5 | `ring_flex` | Ring finger flexion |
| 6 | `pinky_flex` | Little finger flexion |
| 7 | `index_abd` | Index finger abduction |
| 8 | `ring_abd` | Ring finger abduction |
| 9 | `pinky_abd` | Little finger abduction |
| 10 | `thumb_rotation` | Thumb rotation |

<a id="identity"></a>

## Device Identity

`get_device_info()` actively reads device identity information.

```python
# 主动读取设备身份信息
info = hand.get_device_info(timeout=1.0)
# 查看设备身份信息
print(info)
```

<a id="capabilities"></a>

## Device Capabilities

`get_capabilities()` returns basic capability information for the current model. It does not send control commands.

```python
capabilities = hand.get_capabilities()

# View all capability information
print(capabilities)
```

<a id="angle-control"></a>

## Angle Control

### Reading Angles

`get_angles()` reads the current angles once and returns the following fields:

- `raw`: raw angle values returned by the device, ranging from 0 to 255. Smaller values mean more flexion; larger values mean more extension.
- `percent`: converted angle percentages, ranging from 0 to 100. Smaller values are closer to closed; larger values are closer to open.


```python
sample = hand.get_angles(timeout=1.0)  # 读取一次，最多等待 1 秒
print(sample.raw)  # 原始角度值
print(sample.percent)  # 角度百分比
```

### Setting Raw Angles

`set_angles()` accepts 10 raw angle values in `joint_names` order.

```python
# 按 joint_names 顺序设置角度
print(hand.set_angles([128] * 10).wait(timeout=1.0))
# 回读当前角度，核对真机姿态
print(hand.get_angles(timeout=1.0).raw)
```

### Setting Percentage Angles

`set_percent_angles()` accepts 10 angle percentages from 0 to 100. For flexion joints, 0 is the flexed endpoint and 100 is the extended endpoint.

```python
# 按 joint_names 顺序设置百分比角度
print(hand.set_percent_angles([50] * 10).wait(timeout=1.0))
```

<a id="speed-control"></a>

## Speed Control

### Reading Current Speeds

`get_speeds()` reads the current speed of each joint.

```python
# 读取各关节当前速度原始值
print(hand.get_speeds(timeout=1.0).raw)
```

Speed settings control the movement speed of each joint.

### Setting Speeds

`set_speeds()` sets a speed value from 0 to 255 for each joint. After writing, use `get_configured_speeds()` to read the values in the device's response.

```python
# 设置各关节速度
print(hand.set_speeds([100] * 10).wait(timeout=1.0))
```

<a id="acceleration"></a>

## Setting Acceleration

Acceleration settings control how quickly each joint's movement speed changes.

`set_accelerations()` sets an acceleration value from 0 to 254 for each joint. After writing, use `get_configured_accelerations()` to read the values in the device's response.

```python
# 设置各关节加速度
print(hand.set_accelerations([100] * 10).wait(timeout=1.0))
```

<a id="sensor-reading"></a>

## Reading Sensor Data

### Reading Currents

`get_currents()` actively reads the current of each joint. `raw` contains the raw current values from the device protocol.

```python
# 读取各关节当前电流原始值
print(hand.get_currents(timeout=1.0).raw)
```

### Reading Temperatures

`get_temperatures()` reads joint temperatures in degrees Celsius (°C).

```python
# 读取各关节温度
print(hand.get_temperatures(timeout=1.0).raw)
```

### Reading Tactile Arrays

`get_force("fn")` reads the 12×6 tactile array of each of the five fingertips. `fn` denotes the normal tactile array. `force.tactile` preserves its two-dimensional layout, while `force.fn.raw` stores the same device-returned values.

`raw` contains the raw tactile values returned by the device. A field of `None` means no data is available. In `valid_values`, `True` marks a valid value and `False` marks an invalid value.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fn", timeout=1.0)
print(force.fn.raw if force.fn is not None else None)
```

### Reading Normal Summaries

`fns` is the per-finger summary along the pressing direction. It is provided directly by the device; the SDK does not calculate it by summing the tactile array.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fns", timeout=1.0)
print(force.fns.raw if force.fns is not None else None)
```

### Reading Tangential Summaries

`fts` is the per-finger summary along the surface direction. `ft` is the tangential tactile array; these are different data types.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fts", timeout=1.0)
print(force.fts.raw if force.fts is not None else None)
```

### Reading Tangential Directions

`fds` is the per-finger force direction. `valid_values` marks which device-returned values are valid. These numbers are not angles.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fds", timeout=1.0)
print(force.fds.raw if force.fds is not None else None)
```

### Reading Proximity Values

`px` is the proximity sensor data for each finger. It contains raw proximity values from the device protocol.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("px", timeout=1.0)
print(force.px.raw if force.px is not None else None)
```

### Default Tactile Read

`get_force()` reads the normal tactile array `fn` and per-finger normal summaries `fns` by default. Disable any unwanted data in the call.

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force(timeout=1.0)
print(force.fn)
print(force.fns)
```

[Back to overview](../README.md)
