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
| `set_speeds(values)` | `05/06`，各五值 | 0..255 count，物理比例未知；SDK 终态 `DISPATCHED`，不验证写回显，不发布为配置读取值 |
| `set_effort_limits(values)` | `02/03`，各五值 | 原始限制设定，物理量/比例未知；`DISPATCHED`；不是已标定的 N·m 力控 |
| `get_angles(timeout)` | 单字节 `01/04` | 十个原始角度 count；来源 `DEVICE_RESPONSE` |
| `get_speeds(timeout)` | 单字节 `05/06` | 十个实时速度原值；不是速度设定回读，比例未知 |
| `get_effort_limits(timeout)` | 单字节 `02/03` | 十个实时负载原值，`DEVICE_RESPONSE`；保留既有命名，不等于限制设定值；不能直接解释为 mA 或 N·m |
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
| `set_stall_hold_torques(values)` | `C7 GROUP D1..D5`，两帧 | 十个 0..255 原值；堵转后输出扭矩，不是电流，N·m 比例未知 |
| 对应 `get_stall_* (timeout)` | 不发 CAN 请求 | 只返回本次连接内完整确认的写回显；尚无确认值时报告 `UnsupportedOperationError` |

`get_snapshot()` 和 `get_capabilities()` 不发送请求；单位、解释和证据级别可分别通过
`telemetry_channel(...).raw_unit/raw_scale_note/unit_confidence` 查询。角度样本附带固定
百分比投影，但 `raw` 仍是设备原值；无已确认物理映射时 `physical_rad` 为 `None`。

本型号未声明 `get_configured_speeds()`、`set_percent_speeds()`、专用电流/转矩接口、
加速度、堵转维持电流、限位补偿、使能/失能/标定、保存参数和 `clear_faults()`。
这些入口报告 `UnsupportedOperationError`。尤其不能将 L20 的清错帧发送给 L20Lite；
当前资料只定义 `35/36` 查询，没有定义其写入清错契约。

## 堵转配置

堵转配置按照 `get_capabilities().joint_names` 接收十个整数，GROUP=1 为前五关节、
GROUP=2 为后五关节，完整帧 DLC=7，发帧间隔 1 ms。三种设置使用同一 FIFO 配置队列；
只有两组回显均与写入完全一致时，命令才进入 `ACKNOWLEDGED`，并原子更新对应样本。
可通过 `handle.wait(timeout=1.0)` 等待终态，再调用对应 getter 或读取快照。
回显不是主动查询；能力中 `read_mode=COMMAND_RESPONSE`、`device_queried=False`，
`stall_protection.read_supported=False` 表示没有主动回读。
`stall_protection.hold_current=None`，`hold_torque` 给出 C7 的元数据。
设备收到每组参数后立即生效，若第二组缺帧或回显错误，SDK 会报失败并保留上次完整
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
重连后重新判定。身份、角度、故障、负载仍是连接所需基线；链路发送失败按连接故障处理。

## 故障位、单位与轮询

| bit / mask | 协议含义 |
| --- | --- |
| 7 / `80`、6 / `40`、4 / `10` | 未定义 |
| 5 / `20` | 负载过载 |
| 3 / `08` | 过流 |
| 2 / `04` | 过热 |
| 1 / `02` | 磁编码异常 |
| 0 / `01` | 过压或欠压 |

温度与故障定义为 `DOCUMENTED`，不是实测标定过全部故障位；触觉、速度、负载的物理
比例为 `UNKNOWN`，方向比例冲突为 `CONFLICTING`。实机静止时速度零值和小幅变化的负载值支持实时读取语义，但不能据此
确定电流或转矩比例。不能把查询的实时速度/负载写回作为原配置恢复。

单通道声明推荐角度 60 Hz、速度/负载/触觉 30 Hz、温度 1 Hz、故障 5 Hz；
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
| `87/88` 加速度 | 资料列出 5+5 写入与回显，0..254；未定义可靠的原配置读取/恢复，本次不写入 |
| `C5/C6/C7` 堵转配置 | 已按组实现写入和严格回显确认；主动查询和实机恢复验证仍未提供 |
| `C3` 地址、`CC` 回复开关、`CE` 出厂恢复、`CF` 保存 | 会改变路由、通信行为或持久配置；未执行、未新增普通操作接口 |
| `B6` 手掌矩阵 | 资料标为保留，无明确布局 |

本次未做速度/负载配置改写、故障注入、逐轴全行程运动或触觉受压标定。左手证据不能
替代右手或其他固件的硬件验收。
