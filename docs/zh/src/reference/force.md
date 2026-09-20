# 全型号触觉字段与轮询

统一入口为 `hand.get_force(*fields, timeout=1.0)`，字段使用小写字符串，返回原始
协议值。默认读取型号可用的 `fn + fns`；名称统一不代表不同传感器具有相同物理标定。
本页描述开发分支接口，已发布的 2.0.0 尚不包含字段选择和独立字段频率。

## 字段和型号

| 字段 | 含义 | 数据形态 |
| --- | --- | --- |
| `fn` | 法向触觉测点 | 每指一组测点/展平点阵，保留原矩阵或 descriptor 布局 |
| `fns` | 设备上报的法向合力或法向汇总 | 每指一个或多个原始值，不由 SDK 对点阵求和 |
| `ft` / `fts` | 切向测点 / 设备上报的切向汇总 | 每指一组测点 / 一个或多个汇总值 |
| `fd` / `fds` | 切向方向测点 / 切向汇总方向 | 原始方向编码，不自动换算成角度 |
| `px` | 接近感应 | 原始接近值，不推断距离或接触布尔值 |

| 型号 | `fn` 来源与形状 | `fns` | `ft/fts/fd/fds/px` |
| --- | --- | --- | --- |
| L6 | `B1..B5 C6`，每指 12×6 U8 | 不支持 | 不支持 |
| O6 | `B1..B5 A4`，每指 10×4 U8 | 不支持 | 不支持 |
| L20 | `B1..B5 C6`，每指 12×6 U8 | 不支持 | 不支持，不启用保留功能码 |
| L20Lite | `B1..B5 C6`，每指 12×6 U8 | `0x20`，每指一个 U8 原值 | `fts=0x21`、`fds=0x22`、`px=0x23`，每指一个 U8；无 `ft/fd` |
| L30 | 触觉父命令 `0x2`、子命令 1..5，每指 12×6 U8 | 不支持 | 不支持 |
| O20 | 寄存器 `0x09..0x12`，每指在线字节及 12×6 U8 | 不支持 | 不支持；在线标志不能作为 `px` |
| O30 | `MI 0x31` descriptor，`0x32..0x34` 完整原生数据流 | descriptor 定义时支持 | 各字段由 descriptor 决定，不按型号或固件号推断 |

六款矩阵型号的公共 `fn` 是原始计数，不是已标定的 N 或 Pa。单位、比例及可信度
保留在 capability。O30 的力字段保留传感器上报单位，例如 `g`，不自动转为 N；
方向和接近字段没有明确物理单位时返回 `None`，不套用传感器的力单位。

L20Lite 的 `fns/fts/fds/px` 各使用一个独立功能码，单次请求 DLC=1，回复 DLC=6，
五个值依次为拇指、食指、中指、无名指、小拇指。单位标注 `count`；法向/切向标量
资料给出 0..20 N 范围，但比例未经验证，不输出伪装为 N 的原始字节。方向 0..127
为有效编码，255 为未定义，128..254 保留；这些值全部保留，通过 `valid_values`
区分。接近值不直接解释为 mm 或布尔值。SDK 不使用另一套单指 `0x28..0x32` 查询。

O30 的 `FnS_U16_1;Fn_U8_70` 表示每指 1 个小端 U16 合力值和 70 个 U8 测点，
不是 SDK 计算了合力。协议也允许多个 `FnS`，接口不会强行压成一个数。
同一语义有多个 descriptor 段时按段顺序连接；原始段与编码仍在 `sample.sensors` 中。

## 主动查询与返回值

```python
sample = hand.get_force(timeout=1.0)          # 可用的 fn + fns
sample = hand.get_force("fn", timeout=1.0)
sample = hand.get_force("fn", "px", timeout=1.0)  # 型号支持才可使用

if sample.fn is not None:
    print(sample.fn.sensor_names)             # thumb, index, middle, ring, pinky
    print(sample.fn.raw[0])                   # 拇指原始测点元组
    print(sample.fn.units, sample.fn.encodings)
    print(sample.fn.valid_values[0])          # 与 raw 逐值对齐
    print(sample.fn.meta.collected_at, sample.fn.meta.sequence)
if sample.fns is not None:
    print(sample.fns.raw[0])                  # 单个合力也保留元组，例如 (123,)
print(sample.status("fns"))
```

- 读取字段、能力、快照和健康状态都不产生 CAN I/O。
- 字段不支持时为 `None`，`status()` 为 `unsupported`；尚未读取时为 `not_read`。
  成功取得原值为 `available`，断连失效为 `invalidated`。主动读取失败抛异常，不返回
  半成品；缓存保留前一次完整样本，时间戳不被失败或其他字段刷新。
- 固定保留五指位置。缺字段的手指用 `None`，不会补零；有效的数值零仍为零。
  O20 离线测点保留原值，但 `valid_values` 为 False。`meta.valid` 表示完整通信样本，
  不替代逐值有效性、`meta.stale` 或物理标定证据。
- 显式请求已知不支持字段，在发帧前抛 `UnsupportedOperationError`；运行时禁用则抛
  `UnsupportedFirmwareError`。O30 descriptor 未读取时能力为未知，首次查询取得证据后
  才能判定；`supported=None` 不等于不支持，也不等于已验证支持。
- 总 `timeout` 包括排队、指间让步和全部查询。缺片不会发布不完整多指矩阵，不将其他
  轮次的片段拼入本轮；跨连接代次不能组成一个返回值。

O30 的单独 `fn`、单独 `fns` 和两者一起读取，都只执行一次完整原生触觉事务，返回
流中所有已定义字段。事务包含多帧、选指和数据段请求，“一次”不等于“一帧”。
其他型号按支持的独立协议码查询，不为未选择类别增加请求。

保留 `sample.tactile`（矩阵）、`sample.sensors`（O30 descriptor/原始字节）和旧 `raw`
视图；新代码使用 `.fn/.fns/...` 避免混淆。`get_normal_force_resultant(timeout=...)`
保留原有 O30 五指各一个 FnS 的严格契约，新增统一接口不受这个单值限制。

## 轮询与缓存

```python
from linkerbot_hand import PollingPlan

hand.start_polling(
    PollingPlan(
        angles_hz=100,
        force_fn_hz=20,
        temperature_hz=1,
        faults_hz=5,
    )
)
force = hand.get_snapshot().force
```

可选频率为 `force_fn_hz`、`force_fns_hz`、`force_ft_hz`、`force_fts_hz`、
`force_fd_hz`、`force_fds_hz`、`force_px_hz`，未指定或 0 表示不请求该字段。
`force_hz=20` 是默认可用 `fn + fns` 各请求 20 Hz 的简写；不能同时提供正数
`force_fn_hz` 或 `force_fns_hz`，可以和其他受支持字段频率共存。

L20Lite 的默认 getter / `force_hz` 现在同时请求点阵和 `0x20` 标量；需要保持原来
仅点阵的总线开销时使用 `get_force("fn", timeout=...)` / `force_fn_hz`。例如
`PollingPlan(force_fn_hz=20, force_fns_hz=10, force_px_hz=5)` 独立安排三个查询。
固件首次不响应某个可选类别时，按现有连续三次失败策略逐项降级，不影响其他类别。

| 设置 | O30 | 独立协议码型号 |
| --- | --- | --- |
| fn=20，fns=10 | 一组 20 Hz 查询，两字段都随之更新 | fn 20 Hz，fns 10 Hz |
| fn=0，fns=20 | 两字段共用一组 20 Hz 查询 | 仅查询 fns |
| fn=0，fns=0 | 不由这两个字段产生查询；同流其他启用字段仍可带回它们 | 两字段均不查询 |
| force_hz=20 | 一组 20 Hz 查询 | 各受支持默认字段 20 Hz，按实际事务计费 |

O30 其他 descriptor 字段也随完整流读取，共享查询键。首次读取后停止只为不存在字段
安排的查询并告警，保留连接与其他有效轮询。非共享型号的可选查询按功能分别降级，
重连重新探测；成功过的通道不因随后丢帧误报固件不支持。

`health.force_fields` 提供各字段的 `requested_hz`、共用的 `query_hz`、`achieved_hz`
与 `query_channel`。物理查询计数只在 `health.channels` 中记一次；CAN 帧数看
`health.transport`，后者属于整个共享传输口。不要把共用字段的频率相加估算总线开销。
频率是请求目标，不保证组合负载下必然达到。

快照组合各字段最近一次完整查询，使用 `force.fn.meta`、`force.fns.meta` 等各自时间戳
判断新鲜度；`force.meta` 仅描述最后一次完成的物理查询。默认 getter 完成一轮或 O30
来自同一流，也不承诺五指或字段在硬件上同时采样。再次 `start_polling()` 替换整个计划；
`stop_polling()` 停止周期查询但保留缓存。

多字段 getter 的字段年龄包含等待后续查询的时间。运行时将某个可选字段判定为
不支持时，缓存的 `status()` 同步更新；这个状态变化不刷新其他字段的采集时间或序号。

## 能力查询

```python
capability = hand.get_capabilities().force_field("fns")
print(capability.supported)       # True / False / None（等待 descriptor）
print(capability.query_channel)   # 相同值表示共享物理查询
print(capability.shared_fields)
print(capability.sensor_names)
print(capability.firmware_unsupported)
```

能力查询始终零 I/O。需要 O30 实际字段信息时先完成一次 `get_force()`，再获取新的
capability 对象；已取得的不可变 capability 和快照不会被原地更新。
