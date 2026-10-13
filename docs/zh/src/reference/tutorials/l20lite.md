# L20Lite 功能文档

L20Lite 使用经典 CAN，包含 10 个关节。以下示例中的 `hand` 为已连接的设备对象。

## 目录

- [关节顺序](#关节顺序)
- [身份识别](#identity)
- [能力查询](#capabilities)
- [角度控制](#angle-control)
- [速度控制](#speed-control)
- [加速度设置](#acceleration)
- [传感器数据读取](#sensor-reading)

## 关节顺序

SDK 按以下顺序读取和设置关节数据。

| 索引编号 | 关节名称 | 关节 |
| ---: | --- | --- |
| 1 | `thumb_flex` | 拇指弯曲 |
| 2 | `thumb_roll` | 拇指翻滚 |
| 3 | `index_flex` | 食指弯曲 |
| 4 | `middle_flex` | 中指弯曲 |
| 5 | `ring_flex` | 无名指弯曲 |
| 6 | `pinky_flex` | 小指弯曲 |
| 7 | `index_abd` | 食指侧摆 |
| 8 | `ring_abd` | 无名指侧摆 |
| 9 | `pinky_abd` | 小指侧摆 |
| 10 | `thumb_rotation` | 拇指旋转 |

<a id="identity"></a>

## 身份识别

`get_device_info()` 主动读取设备身份信息。

```python
# 主动读取设备身份信息
info = hand.get_device_info(timeout=1.0)
# 查看设备身份信息
print(info)
```

<a id="capabilities"></a>

## 能力查询

`get_capabilities()` 查询当前型号的基本能力信息。此方法不发送控制命令。

```python
capabilities = hand.get_capabilities()

# 查看全部能力信息
print(capabilities)
```

<a id="angle-control"></a>

## 角度控制

### 角度读取

`get_angles()` 读取一次当前角度，返回以下字段：

- `raw`：设备返回的原始角度值，范围为 0～255，数值越小越弯曲，越大越伸直。
- `percent`：换算后的角度百分比，范围为 0～100，数值越小越接近闭合，越大越接近张开。


```python
sample = hand.get_angles(timeout=1.0)  # 读取一次，最多等待 1 秒
print(sample.raw)  # 原始角度值
print(sample.percent)  # 角度百分比
```

### 原始角度设置

`set_angles()` 接收 10 个原始角度值，顺序见 `joint_names`。

```python
# 按 joint_names 顺序设置角度
print(hand.set_angles([128] * 10).wait(timeout=1.0))
# 回读当前角度，核对真机姿态
print(hand.get_angles(timeout=1.0).raw)
```

### 百分比角度设置

`set_percent_angles()` 接收 10 个 0 到 100 的百分比角度。屈伸关节中，0 表示弯曲端，100 表示伸直端。

```python
# 按 joint_names 顺序设置百分比角度
print(hand.set_percent_angles([50] * 10).wait(timeout=1.0))
```

<a id="speed-control"></a>

## 速度控制

### 实时速度读取

`get_speeds()` 读取各关节当前速度。

```python
# 读取各关节当前速度原始值
print(hand.get_speeds(timeout=1.0).raw)
```

速度设置用于控制各关节的运动速度。

### 速度设置

`set_speeds()` 设置各关节的速度值，范围为 0 到 255。写入后可用 `get_configured_speeds()` 读取设备回复的值。

```python
# 设置各关节速度
print(hand.set_speeds([100] * 10).wait(timeout=1.0))
```

<a id="acceleration"></a>

## 加速度设置

加速度设置用于控制各关节运动速度的变化快慢。

`set_accelerations()` 设置各关节的加速度值，范围为 0 到 254。写入后可用 `get_configured_accelerations()` 读取设备回复的值。

```python
# 设置各关节加速度
print(hand.set_accelerations([100] * 10).wait(timeout=1.0))
```

<a id="sensor-reading"></a>

## 传感器数据读取

### 电流读取

`get_currents()` 主动读取各关节当前电流，`raw` 保存设备协议中的电流原始值。

```python
# 读取各关节当前电流原始值
print(hand.get_currents(timeout=1.0).raw)
```

### 温度读取

`get_temperatures()` 读取各关节温度，单位为摄氏度（℃）。

```python
# 读取各关节温度
print(hand.get_temperatures(timeout=1.0).raw)
```

### 触觉点阵读取

`get_force("fn")` 读取五个指尖的 12×6 触觉点阵。`fn` 表示法向触觉点阵；`force.tactile` 保留二维排列，`force.fn.raw` 保存同一批设备返回值。

`raw` 保存设备返回的触觉原始值。字段为 `None` 表示没有数据；`valid_values` 中的 `True` 表示对应数值有效，`False` 表示无效。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fn", timeout=1.0)
print(force.fn.raw if force.fn is not None else None)
```

### 法向汇总读取

`fns` 表示每根手指沿按压方向的汇总值。该值由设备直接提供，不是 SDK 对触觉点阵求和的结果。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fns", timeout=1.0)
print(force.fns.raw if force.fns is not None else None)
```

### 切向汇总读取

`fts` 表示每根手指沿表面方向的汇总值；`ft` 则是切向触觉点阵，两者是不同数据。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fts", timeout=1.0)
print(force.fts.raw if force.fts is not None else None)
```

### 切向方向读取

`fds` 表示每根手指的受力方向。返回结果中的 `valid_values` 标记哪些设备返回值有效；这些数字不是角度。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("fds", timeout=1.0)
print(force.fds.raw if force.fds is not None else None)
```

### 接近值读取

`px` 表示每根手指的接近传感器数据，返回值是设备协议中的接近原始值。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force("px", timeout=1.0)
print(force.px.raw if force.px is not None else None)
```

### 默认触觉读取

`get_force()` 默认读取法向触觉点阵 `fn` 和每根手指的法向汇总值 `fns`。不需要某项数据时，可在调用中将其关闭。

```python
# 读取指定触觉数据，不对点阵求和
force = hand.get_force(timeout=1.0)
print(force.fn)
print(force.fns)
```

[返回总览](../README.md)
