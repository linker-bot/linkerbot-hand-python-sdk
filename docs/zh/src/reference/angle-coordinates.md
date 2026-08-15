# 角度坐标、方向与用户映射

`linkerbot-hand` 同时保留统一接口和旧型号接口。两套接口都可能出现 `raw` 一词，但它们
并不总是表示同一坐标。新代码必须区分百分比、`standard_raw` 和 `protocol_raw`。

## 三种角度坐标

| 名称 | 范围与单位 | 精确定义 | 推荐用途 |
| --- | --- | --- | --- |
| 百分比位置 | `0..100`，无量纲 | unified 屈伸轴固定 `0=弯曲/闭合，100=伸直/张开`；方向轴的端点由 capability 描述 | ROS 和新应用的常规控制 |
| `standard_raw` | classic 型号为 `0..255` 整数 | 旧 manager 的历史逻辑坐标；发送前仍经过用户 LUT，不是设备线上精确值 | 仅兼容已有 classic 校准应用 |
| `protocol_raw` | 范围和单位逐关节读取 capability | CAN/CAN FD 协议实际发送和读取的原始位置值，也称 hardware raw | 协议调试、标定和无损透传 |

只有 L6、O6、L20Lite 和 L25 的旧 manager 存在 `standard_raw` 用户 LUT。L30、O20、
O30i 的旧 `set_raw_angles()` 本身使用协议原值，不经过这套 classic LUT。

## 统一接口

统一接口只公开百分比位置和协议原值，不公开 `standard_raw`：

```python
from linkerbot_hand import PollingPlan
from linkerbot_hand.backends.legacy import create_hand_device

hand = create_hand_device("L6", side="left", interface_name="can0")
hand.open()
try:
    hand.start_polling(PollingPlan(angles_hz=60))

    percent_handle = hand.submit_angles([100, 50, 100, 100, 100, 100])
    protocol_handle = hand.submit_raw_angles([255, 128, 255, 255, 255, 255])

    sample = hand.get_snapshot().angles
    if sample is not None and sample.valid:
        print(sample.values)      # 映射后的百分比位置
        print(sample.raw_values)  # 精确 protocol_raw；后端不提供时为 None
finally:
    hand.stop_polling()
    hand.close()
```

- `submit_angles()` 按每个 `JointCapability.percentage_mapping` 把百分比换算为
  `protocol_raw`。
- `submit_raw_angles()` 直接提交 `protocol_raw`，绕过 classic 用户 LUT。
- 角度样本的 `values` 是映射后的百分比位置，`raw_values` 是同次采集的精确
  `protocol_raw`。
- `get_snapshot()` 只读取缓存，不发送设备请求；禁用轮询后不会产生角度查询帧。

逐关节原始范围、整数约束、方向和端点必须从 `get_capabilities().joints` 读取，不得在 ROS
或应用中复制一份型号表。没有可靠物理标定的型号不会把协议值伪装成度或弧度。

## classic 旧接口与用户 LUT

L6、O6、L20Lite 和 L25 的旧 manager 按下面的路径处理角度：

```text
set_angles(percent)
    -> standard_raw
    -> 每关节用户 LUT
    -> protocol_raw
    -> 设备

set_raw_angles(standard_raw)
    -> 每关节用户 LUT
    -> protocol_raw
    -> 设备

set_hardware_raw_angles(protocol_raw)
    -> 设备
```

因此，旧接口的 `set_raw_angles()` 不是精确协议原值入口。需要绕过 LUT 时必须调用
`set_hardware_raw_angles()`；迁移到 unified 后则调用 `submit_raw_angles()`。

用户 LUT 是 `关节数 × 256` 的整数矩阵：

```python
mapping[joint_index][standard_raw] = protocol_raw
```

例如 `mapping[0][128] = 150` 时：

```python
hand.angle.set_angle_mapping(mapping)
hand.angle.set_raw_angles([128] * 6)           # L6 第一轴实际发送 150
hand.angle.set_hardware_raw_angles([128] * 6)  # L6 第一轴实际发送 128
```

默认 LUT 是恒等映射，所以未配置时两种 raw 的数值相同。LUT 可用于逐轴非线性校准、反向、
偏移或死区补偿：

```python
mapping = hand.angle.get_angle_mapping()
default_mapping = hand.angle.get_default_angle_mapping()
hand.angle.set_angle_mapping(mapping)
hand.angle.reset_angle_mapping()
```

每行必须包含 256 个 `0..255` 整数，但不要求单调或一一对应。设备回读时，旧 manager 保存
精确 `raw_angles`，再用逆 LUT 得到 `standard_raw_angles`；若 LUT 存在重复或缺失值，逆向
选择距离最近的 standard raw，距离相同时选择较小值。因此只有 `raw_angles` 能无损表示线上
协议值。

映射默认保存在 `$XDG_CONFIG_HOME/linkerbot/hand_angle_mappings.toml`；未设置
`XDG_CONFIG_HOME` 时使用 `~/.config/linkerbot/hand_angle_mappings.toml`。配置按
`model:side:interface_name` 隔离，并通过文件锁和原子替换写入。

## ROS 2 接口边界

ROS 只应暴露两套明确命名的坐标：

| ROS 数据 | SDK 来源 | 坐标 |
| --- | --- | --- |
| 百分比角度命令 | `submit_angles()` | unified 百分比 |
| 原始角度命令 | `submit_raw_angles()` | `protocol_raw` |
| 百分比角度状态 | `snapshot.angles.values` | unified 百分比 |
| 原始角度状态 | `snapshot.angles.raw_values` | `protocol_raw` |

ROS 不应暴露 `standard_raw`，也不应读取 classic LUT 文件。百分比和协议 raw topic 必须使用
不同名称，避免把未标定的协议值解释为物理角度，或在迁移时重复应用 LUT。
