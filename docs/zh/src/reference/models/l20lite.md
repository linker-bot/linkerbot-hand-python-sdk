# L20Lite 接口、原始单位与实机边界

触觉统一通过 `get_force(*fields, timeout=1.0)` 获取：点阵为 `fn`，五指法向汇总为
`fns`，切向汇总为 `fts`，切向汇总方向为 `fds`，接近值为 `px`；无逐测点 `ft/fd`。
默认读取 `fn + fns`，完整公共契约见[全型号触觉接口](../force.md)。

本文对照 LHT10 协议和 L20Lite 左手实机描述当前 SDK。
[固件支持记录](./l20-family-firmware-support.md) 仅记录证据，不是版本准入限制。

## 连接与帧布局

- 构造使用 `model="L20Lite"`（也接受 `l20-lite`），须指定 `left` 或 `right`。
- 经典 CAN、标准帧、1 Mbit/s，左手 `0x28`，右手 `0x27`；调用方须先配置并启用接口。
- 出厂型号前缀为 `LHT10`。实机 `LHT10-07-0-L-B-1-A` 恰为 18 字节，只返回三片完整
  `C0` 响应，没有第四片或 NUL；SDK 接受完整七字段编码，长编码缺末片仍不完整。
- 多帧向量的相邻请求间隔至少 1 ms。固件 4.6.10 在原 0.5 ms 间隔下可偶发遗漏回复；
  不能用增加响应超时替代正确发送节奏。

公共十维向量顺序：

| index | 名称 |
| ---: | --- |
| 0 | thumb_flex |
| 1 | thumb_roll |
| 2 | index_flex |
| 3 | middle_flex |
| 4 | ring_flex |
| 5 | pinky_flex |
| 6 | index_abd |
| 7 | ring_abd |
| 8 | pinky_abd |
| 9 | thumb_rotation |

角度的 `01` 覆盖 index 0..5，`04` 覆盖 6..9（**6+4**）。其余向量第一片覆盖 0..4，
第二片覆盖 5..9（**5+5**）。两种布局已独立抓帧核对，不能复用一个固定拆分位置。
这是数字通道顺序；本轮没有逐轴运动确认所有物理名称和角度标定。

## 当前开放接口

| 接口 | opcode / 数据 | 单位、语义及成功含义 |
| --- | --- | --- |
| `set_angles(values)` | `01` 六值 + `04` 四值 | `u8`，0..255 count；写回复为当前角度，来源 `DEVICE_COMMAND_FEEDBACK`；`returns_current_state=True`；`DISPATCHED` 不等于机械到位 |
| `set_percent_angles(values)` | 十维 0..100 固定映射到上述原值 | 无 LUT；0%=raw 0，100%=raw 255。侧摆/旋转轴方向查 `percent_mapping`，不要把全部 255 当作通用张开姿态 |
| `set_speeds(values)` | `05/06`，各五值 | 0..255 count，物理比例未知；收齐有效写回复后 `ACKNOWLEDGED`，实际回值保存为配置样本 |
| `get_configured_speeds(timeout)` | 不发 CAN 请求 | 本次连接内最近完整写回显，来源 `DEVICE_CONFIG_RESPONSE`；不是实时速度 |
| `set_accelerations(values)` | `87/88`，各五值、DLC=6 | 写入十个 0..254 count；收齐有效回复后 `ACKNOWLEDGED`，保留设备实际返回值，物理比例未知 |
| `get_configured_accelerations(timeout)` | 不发 CAN 请求 | `ConfiguredAccelerationSample`；本次连接内最近完整确认的写回显，来源 `DEVICE_CONFIG_RESPONSE`，不是实时加速度 |
| `set_current_limits(values)` | `02/03`，各五值 | 电流限制原始 count，比例未知；收齐有效写回复后 `ACKNOWLEDGED` |
| `get_current_limits(timeout)` | 不发 CAN 请求 | 本次连接内最近完整写回显，来源 `DEVICE_CONFIG_RESPONSE`；不是实时电流 |
| `get_angles(timeout)` | 单字节 `01/04` | 十个原始角度 count；来源 `DEVICE_RESPONSE` |
| `get_speeds(timeout)` | 单字节 `05/06` | 十个实时速度原值；不是速度设定回读，比例未知 |
| `get_currents(timeout)` | 单字节 `02/03` | 十个实时电流原始 count，`DEVICE_RESPONSE`；不等于限制设定值，不自动换算成 mA |
| `get_temperatures(timeout)` | 单字节 `33/34` | 十个 `u8`，`degree_celsius`，1 ℃/count；不额外换算 |
| `get_faults(timeout)` | 单字节 `35/36` | 十个 `u8_bitmask`，无物理单位，位定义见下表 |
| `get_force("fn", timeout=...)` | `B1 C6` 至 `B5 C6` | 五指各 12×6 `u8` 原始矩阵；count，物理标定未知 |
| `get_force("fns", timeout=...)` | `20`，请求 1 byte、回复 6 bytes | 五指各一个法向汇总原值；u8 count，资料范围 0..20 N，比例未经验证，不由矩阵求和 |
| `get_force("fts", timeout=...)` | `21`，请求 1 byte、回复 6 bytes | 五指各一个切向汇总原值；u8 count，资料范围 0..20 N，比例未经验证 |
| `get_force("fds", timeout=...)` | `22`，请求 1 byte、回复 6 bytes | 五指各一个切向方向编码；0..127 有效，255 未定义，128..254 保留；均保留原值并单独标注有效性 |
| `get_force("px", timeout=...)` | `23`，请求 1 byte、回复 6 bytes | 五指各一个接近原值；u8 count，无已确认距离比例或布尔阈值 |
| `get_device_info(timeout)` | `C0/C1/C2/C4` | 产品编号、PCB/固件/机械版本与工厂字段；三个版本字节按 major/minor/patch 解释 |
| `set_stall_thresholds(values)` | `C5 GROUP D1..D5`，两帧 | 十个 0..255 原值；堵转保护阈值，物理比例未知 |
| `set_stall_detection_times(values)` | `C6 GROUP D1..D5`，两帧 | 十个 0..255 原值；每 count 为 10 ms，SDK 不换算 |
| `set_stall_hold_currents(values)` | `C7 GROUP D1..D5`，两帧 | 十个 0..255 原值；堵转后维持电流，资料旧称“输出扭矩”，物理电流比例未知 |
| 对应 `get_stall_* (timeout)` | 不发 CAN 请求 | 只返回本次连接内完整确认的写回显；尚无确认值时报告 `UnsupportedOperationError` |

`get_snapshot()` 和 `get_capabilities()` 不发送请求；单位、解释和证据级别可分别通过
`telemetry_channel(...).raw_unit/raw_scale_note/unit_confidence` 查询。角度样本附带固定
百分比投影，但 `raw` 仍是设备原值；无已确认物理映射时 `physical_rad` 为 `None`。

本型号未声明转矩读写、
限位补偿、使能/失能/标定、保存参数和 `clear_faults()`。
这些入口报告 `UnsupportedOperationError`。尤其不能将 L20 的清错帧发送给 L20Lite；
L20Lite 没有清除错误码命令，`35/36` 仅用于查询。SDK 不发送猜测的清错帧。
电流查询使用 `PollingPlan(current_hz=...)`，样本在 `snapshot.currents`；
没有主动限制配置查询，不能用实时电流恢复原配置。

## 速度和电流限制的写回显

`05/06` 和 `02/03` 的写回复分别表示速度设定和电流限制；同功能码的裸查询回复却表示
实时速度和实时电流。SDK 将两组完整写回显保存到 `snapshot.configured_speeds` 或
`snapshot.current_limits`，不更新 `snapshot.measured_speeds` / `snapshot.currents`。
`get_configured_speeds()` / `get_current_limits()` 的能力为 `COMMAND_RESPONSE`，仅返回
本次连接内的完整确认样本，不发查询、不加入基线或轮询；尚无样本时报告
`UnsupportedOperationError`，恢复连接会清空样本且不重放设置。

这两类设置的成功终态现在为 `ACKNOWLEDGED`；`DISPATCHED` 只表示两个请求均已发送。
SDK 透传设备实际回值，不要求与发送值相等，也不由目标值构造样本。
缺组、错误长度或冲突重复回复使命令失败，保留上次完整配置样本；设备可能已经应用
部分设置，旧样本不能证明当前完整配置。SDK 不自动重试、补零、回滚或保存 Flash。

失败后的同功能码读写先经过 100 ms 固定迟到隔离，再允许下一事务发送；该窗口中的
残片、畸形回复和冲突重复帧只丢弃，不发布、不提前结束隔离，也不会延长隔离窗口。
正常完整回显不增加隔离等待，不增加 CAN 请求。响应等待本身为 100 ms，独立于这段
失败后的隔离时间；getter 调用者的等待超时也独立计算。
协议没有请求序号，无法区分超出隔离窗口后才到达、并与新查询重叠的旧回复；不承诺
任意延迟下绝不混淆。该隔离时长是 SDK 有界策略，不是已验证的固件最大延迟。
本次用设定值和实测值不同的模拟设备覆盖延迟、缺组、畸形和乱序，未进行实机写入。

故障样本 `sample.raw` 保留十关节原始字节；`sample.statuses` 按同一顺序返回
`L20LiteFaultStatus`。可读取 `load_overload`（负载过载）、`over_current`（过流）、
`over_temperature`（过热）、`magnetic_encoder_abnormal`（磁编码异常）、
`voltage_abnormal`（过压或欠压，协议无法进一步区分）。`reserved_bits` 保留未定义的
bit7/6/4，`descriptions` 返回所有已触发错误的英文解释，含未知位的十六进制提示；
原始字节为 0 时该元组为空。此类型的位定义与 O30 的 `JointFaultStatus` 不同。

```python
faults = hand.get_faults(timeout=1.0)
for joint, status in zip(hand.get_capabilities().joints, faults.statuses):
    print(joint.name, hex(status.raw), status.descriptions)
```

## 加速度配置

`set_accelerations(values)` 接收按 `joint_names` 排列的十个整数，原始范围为 0..254。
`87 A1 A2 A3 A4 A5` 设置前五关节，`88 A6 A7 A8 A9 A10` 设置后五关节，帧间隔
至少 1 ms；没有 GROUP 字节。SDK 保留原值，不归一化、不换算为 °/s²，也不将 O6
的零值特殊语义套用于 L20Lite。单位为 `count`，物理比例标记为 `UNKNOWN`。

```python
from linkerbot_hand import CommandStage

handle = hand.set_accelerations([16] * 10)
if handle.wait(timeout=1.0) is CommandStage.ACKNOWLEDGED:
    sample = hand.get_configured_accelerations(timeout=1.0)
    print(sample.raw)  # 十关节最近一次完整设备回复，可能不同于发送值
```

加速度使用有界 FIFO，连续配置不会被新值覆盖。两组回复完整且帧结构正确后，按关节
顺序用实际返回值更新 `snapshot.configured_accelerations`，不要求等于发送值。
`ACKNOWLEDGED` 表示收到完整有效回复，不证明设定值已原样生效。结构正确但数值超出
声明范围的回复也保留原值，并标记 `meta.within_declared_range=False`。
缺帧、长度错误或冲突重复帧使命令失败，保留上次完整样本；设备可能已应用其中一组，
失败不表示设备参数未变化，旧样本也不能证明当前全部配置。无隐式重试、回滚或持久保存。

协议只明确设置与回显，SDK 不发送裸 `87` / `88` 查询，不加入基线或周期轮询。
能力中 `read_mode=COMMAND_RESPONSE`、`device_queried=False`、`device_measured=False`；
getter 只返回本次连接内最近确认的写回显，可能随时间变旧。尚未获得完整回显时报告
`UnsupportedOperationError`；恢复连接会清空缓存且不会重放设置。无法据此读取或恢复
连接前的未知配置。

2026-09-21 在 can0 的左手 `LHT10-07-0-L-B-1-A`、固件 4.6.10 上完成原始帧和 SDK
对照：三次十关节设置共六帧准确回显，20 次 getter/快照读取未增加 CAN 帧，重连清空
缓存；未发送运动指令，前后角度相同、故障均为零。测试最后写入十个原值 16，未保存
Flash，也未声称恢复未知初值。0/254 边界由模拟测试覆盖，实机只验证回显契约，未验证
物理加速度或其他固件；详见[固件支持记录](./l20-family-firmware-support.md)。

## 堵转配置

堵转配置按照 `get_capabilities().joint_names` 接收十个整数，GROUP=1 为前五关节、
GROUP=2 为后五关节，完整帧 DLC=7，发帧间隔 1 ms。三种设置使用同一 FIFO 配置队列；
只有两组有效回复完整时，命令才进入 `ACKNOWLEDGED`，并按组号原子发布实际返回值，
不要求与发送值一致，也不以发送目标填充样本。相同重复帧允许，冲突重复帧仍报错。
`ACKNOWLEDGED` 不表示所有设置已原样生效。
可通过 `handle.wait(timeout=1.0)` 等待终态，再调用对应 getter 或读取快照。
回显不是主动查询；能力中 `read_mode=COMMAND_RESPONSE`、`device_queried=False`，
`stall_protection.read_supported=False` 表示没有主动回读。
`stall_protection.hold_current` 给出 C7 元数据，样本为 `StallHoldCurrentSample`，
快照字段为 `stall_hold_currents`；原 `stall_hold_torques` 名称已删除。
设备收到每组参数后立即生效，若第二组缺帧或回复结构错误，SDK 会报失败并保留上次完整
确认样本，但不能撤销设备已应用的第一组；不要把失败解释为“设备没有变化”。
本次实现没有执行真实设备参数写入或验证物理安全范围，不提供隐式重试和持久保存。

## 触觉和可选查询

仅点阵查询的 `ForceSample.raw`、`sensors` 为空；`tactile` 中的五项按 thumb、index、middle、ring、pinky
排列，每项 `raw` 是 12×6 点阵。SDK 按行标记组装，支持乱序、拒绝冲突的重复行。
每指完整后再请求下一指，指间可调度有界数量的完整角度读写；全部五指完成才更新样本。
缺行不会补零或发布部分矩阵，缓存保留上一次完整值并继续按时间变旧。

协议为标量力对象 `20/21` 描述了 0..20 N，**这个范围不适用于 `B1..B5` 点阵**。
固件 4.6.10 的历史实机验证中点阵可读，但上述标量对象逐项 150 ms 内无回复；
这是该实机的记录，不限制其他固件使用新增类别查询，也不能把点阵包装成标量合力。

`sample.fn.raw` 为每指展平的点阵；`sample.fns.raw`、`fts.raw`、`fds.raw`、`px.raw`
为每指单值元组，例如 `((12,), (0,), (8,), (3,), (1,))`。各类别保留独立时间戳；
只读取标量时，旧 `sample.raw` 为最后一个类别的五值向量，新代码应使用命名字段。
原始方向文字范围为 0..359°，原表给出的 2.834645669291339°/count 则覆盖 0..360°；
该冲突在能力中标为 `CONFLICTING`，不自动换算。原图方向上=0°、右=90°、下=180°、左=270°。

`get_force()` 默认额外读取 `0x20`，`force_hz` 也同时调度点阵和法向汇总。
需要仅点阵时使用 `get_force("fn", timeout=...)` / `PollingPlan(force_fn_hz=...)`。
各类别可独立设置 `force_fn_hz/force_fns_hz/force_fts_hz/force_fds_hz/force_px_hz`；
未选择类别不发请求，读快照字段不发请求。每个标量事务只有一条类别请求，没有单指补查。

实时速度、温度、点阵及四个标量类别均为独立可选查询：当前连接从未成功的通道连续三次超时或结构错误后，
报告 `UnsupportedFirmwareError`，停止该通道查询并保留连接；成功过的通道后续丢帧不降级。
重连后重新判定。身份、角度、故障、电流仍是连接所需基线；链路发送失败按连接故障处理。

## 故障位、单位与轮询

| bit / mask | 协议含义 |
| --- | --- |
| 7 / `80`、6 / `40`、4 / `10` | 未定义 |
| 5 / `20` | 负载过载 |
| 3 / `08` | 过流 |
| 2 / `04` | 过热 |
| 1 / `02` | 磁编码异常 |
| 0 / `01` | 过压或欠压 |

温度与故障定义为 `DOCUMENTED`，不是实测标定过全部故障位；触觉、速度、电流的物理
比例为 `UNKNOWN`，方向比例冲突为 `CONFLICTING`。历史实机的速度零值和小幅变化的
电流原值支持实时读取语义，但没有确认物理比例，不能据此恢复原配置。

单通道声明推荐角度 60 Hz、速度/电流/触觉 30 Hz、温度 1 Hz、故障 5 Hz；
向量估时 6 ms、五指触觉整次估时 20 ms，响应超时分别为向量 100 ms、触觉每指 150 ms。
每个新增标量类别单通道推荐 30 Hz，估时 2 ms，响应超时 100 ms；未作实机上限验证。
估时是调度元数据，推荐值不是组合负载的稳定承诺。本次空闲触觉调用约 21..35 ms，
100 Hz 角度写入与 30 Hz 触觉组合仍出现缺行，降低至 20 Hz 触觉也未完全消除。
SDK 未发布残缺矩阵，独立原始抓包同样缺行；尚不能将根因限定在固件或适配器。
绕过 Hand SDK、逐指原始请求并在每指后停留 2 ms 的 500 次查询中也有 2 次缺行。
30 Hz 推荐值及 20 ms 估时仍需多轮复核，`verified_max_hz` 没有被填成已验证上限。

## 尚未开放的协议对象

| 对象 | 当前证据与边界 |
| --- | --- |
| `28/29/30/31/32` 单指四类传感数据 | 同一数据的另一种查询方式；本轮只采用 `20..23` 类别查询，不开放或隐式补发单指查询 |
| `C5/C6/C7` 堵转配置 | 已按组实现写入和完整回复组装，保留实际返回值；主动查询和实机恢复验证仍未提供 |
| `C3` 地址、`CC` 回复开关、`CE` 出厂恢复、`CF` 保存 | 会改变路由、通信行为或持久配置；未执行、未新增普通操作接口 |
| `B6` 手掌矩阵 | 资料标为保留，无明确布局 |

本次未做速度/电流配置改写、故障注入、逐轴全行程运动或触觉受压标定。左手证据不能
替代右手或其他固件的硬件验收。
