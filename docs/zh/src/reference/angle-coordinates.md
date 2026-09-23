# 协议原值、percent 与角度方向

Hand SDK 只保留两种角度命令表示：

1. `set_angles()`：设备协议原值。
2. `set_percent_angles()`：SDK 固定 capability 映射后的 `0..100`。

不再存在 LUT、“标准 raw”、用户 raw、hardware raw 或可修改的外部映射文件。

## 协议原值

```python
handle = hand.set_angles([255, 128, 255, 255, 255, 255])
```

`set_angles()` 只检查向量长度、有限数、整数约束和逐通道协议范围，然后把数值原样交给
对应 protocol codec。它不做方向翻转、percent 换算、物理单位换算或 clamp。

“raw”在公开契约中只表示一种内容：设备线上协议对象的解码数值。不存在另一个
`set_hardware_raw_angles()`。

## percent 角度

```python
handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])
```

输入必须是有限的 `float/int 0..100`。屈伸轴统一为：

- `0`：完全弯曲或闭合。
- `100`：完全伸直或张开。

拇指侧摆、手指侧摆、旋转、横滚和腕关节不能解释为开合。它们的两端由每个
`JointCapability.percent_mapping` 明确描述：

```python
for joint in hand.get_capabilities().joints:
    mapping = joint.percent_mapping
    print(
        joint.name,
        mapping.raw_at_zero if mapping else None,
        mapping.raw_at_hundred if mapping else None,
        mapping.zero_label if mapping else None,
        mapping.hundred_label if mapping else None,
        mapping.confidence if mapping else None,
    )
```

线性映射为：

```text
raw = raw_at_zero + percent / 100 * (raw_at_hundred - raw_at_zero)
```

整数协议按 capability 的 `Quantization` 规则量化；默认 nearest 使用 ties away from
zero，不依赖 Python `round()` 的 banker's rounding。

百分比映射是随 SDK profile 版本管理的协议事实，用户不能运行时修改。

## 一次读取返回所有可信表示

```python
angles = hand.get_angles(timeout=0.2)

print(angles.raw)
print(angles.percent)
print(angles.physical_rad)
print(angles.meta)
```

一次设备事务生成同一个 `AngleSample`：

- `raw` 永远保留设备真实回复。
- `percent` 在有可靠端点的轴上返回 `float`，否则为 `None`。
- `physical_rad` 只有存在可靠物理标定时返回弧度，否则为 `None`。

读取侧不会另设 `get_percent_angles()`，避免为了不同表示重复查询设备。缓存中的
`hand.get_snapshot().angles` 使用相同数据结构。

## 范围处理

命令和反馈有意采用不同策略：

- `set_angles()` 只接受 capability 声明范围内的协议值。
- `set_percent_angles()` 只接受 `0..100`。
- 设备反馈即使超出声明范围也保留真实 `raw`，不 clamp。

超范围反馈的 `SampleMeta.within_declared_range=False`。其线性 percent 结果可能超出
`0..100`，用于保留故障、标定漂移或协议变化证据。

## 其他通道

普通速度、电流、电流上限、转矩上限、转矩数据、温度、触觉、运动时间和堵转配置
接口都只返回协议 raw，不在 SDK 运行时生成另一套物理值。

特别注意：

- 配置速度不等于实测速度。
- 电流上限不等于机械扭矩。
- O20 的 `set_speeds()` 使用 `0..100` 范围内的协议原值。
- 协议 count 有 RPM/mA 比例说明时，比例属于 capability metadata，不改变返回值。
- 单位或物理含义未确认时标记为 unknown/conflicting，不能伪装成弧度、RPM、mA 或 N.m。

## 采集与回放

记录 raw 或 percent 数据时同时保存：

- 型号、固件、side、sensor variant 和 connection generation；
- capability revision 所代表的关节顺序、raw 范围和 percent 端点；
- `SampleMeta.sequence`、`collected_at`、`valid`、`stale` 和 `origin`；
- 值的表示类型是 raw 还是 percent。

不要在同一字段混放 raw、percent 和物理值。跨 SDK 版本回放 percent 时，应保留当时的
capability/profile 信息。
