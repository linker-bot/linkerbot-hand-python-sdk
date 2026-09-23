# 自动发现与身份识别

`discover_hands()` 是显式调用的主动发现函数。导入 `linkerbot_hand`、构造 transport 或
构造 `HandDevice` 都不会扫描总线。默认 `PlatformDiscoverySource` 根据操作系统组合
可用后端。SocketCAN 只读取已启用接口，不执行 `ip link set`；USB 来源按显式配置打开
适配器，退出探测时关闭。缺库、占用或单后端失败进入报告，不丢弃其他后端的结果。

当前维护范围为一通道一只手；多只手使用不同 CAN/CAN FD 接口。

## 平台与适配器配置

| 来源 | 默认平台 | 枚举方式 | 默认探测策略 | 报告通道名示例 |
| --- | --- | --- | --- | --- |
| SocketCAN | Linux | 已处于 UP 状态的 CAN 网络接口 | classic 接口只 CAN；FD 接口先 CAN 再 FD | `can0` |
| PCAN | Windows | PCANBasic 附加通道表，只选可用通道 | CAN；可显式启用 FD | `pcan:PCAN_USBBUS1` |
| SLCAN | Windows | 已知 USB 候选或指定串口 | FD；可显式选择 CAN | `slcan:COM5` |
| MiniCANFD | Linux、Windows | 厂商库扫描与通道类型检查 | 仅 FD | `minicanfd:0:0` |

`DiscoveryConfig.backends` 限定来源；`channels` 在汇总后筛选上述报告通道名。
后端配置的 `channels` 则使用原生名称，例如 `PCAN_USBBUS1` 或 `COM5`。未枚举到指定
通道时报告 `unavailable`，不探测其他通道。PCAN/SLCAN 的原生枚举不打开总线或串口，
实际探测阶段才初始化适配器并发送身份请求。

```python
from linkerbot_hand import DiscoveryConfig, PythonCANDiscoveryConfig, discover_hands

report = discover_hands(
    DiscoveryConfig(
        backends=("pcan", "slcan"),  # Windows 默认还包含 minicanfd
        pcan=PythonCANDiscoveryConfig(channels=("PCAN_USBBUS1",)),
        slcan=PythonCANDiscoveryConfig(
            channels=("COM5",),
            bus_options={"tty_baudrate": 460800},  # 串口速率，与 CAN bitrate 独立
        ),
        total_timeout_s=30.0,
    )
)
```

PCAN 需安装 PCANBasic 动态库和设备驱动。SLCAN 需安装 pyserial，自动模式仅筛选
`16D0:117E` CANable 2.0 USB 候选；其他串口需明确指定。`supports_fd=None` 表示
固件能力未知，USB 标识不保证支持 FD；FD 探测需要 CANable 2.0 FD 扩展固件。
不会向机器上的所有串口发送初始化指令。

需要改变某个后端的顺序时，可设置
`PythonCANDiscoveryConfig(probe_protocols=(TransportKind.CAN, TransportKind.CAN_FD))`，
或反向顺序、单一协议；`TransportKind` 从 `linkerbot_hand` 导入。PCAN 只有驱动报告
FD 能力时才启用 FD；FD-only 策略遇到经典 CAN 适配器时报告 `unsupported_protocol`。
两种模式会分别打开和关闭，不同时占用串口/PCAN 通道。默认 nominal/data 速率为
1/5 Mbit/s，可用 `bitrate`、`data_bitrate` 修改；PCAN FD 可传 `fd_timing`，未传时
使用 80 MHz 时钟生成匹配速率的 timing。`bus_options` 仅接受不可变标量值，
`channel`、`bitrate`、`timing` 等专门配置项不能重复放入该映射。

Linux MiniCANFD 需要明确有效通道，不能从设备数量推断每台设备有几路：

```python
from linkerbot_hand import DiscoveryConfig, MiniCANFDDiscoveryConfig, discover_hands
from linkerbot_transport.canfd import CANFDConfigOptions

report = discover_hands(
    DiscoveryConfig(
        backends=("minicanfd",),
        minicanfd=MiniCANFDDiscoveryConfig(
            library_path="/opt/minicanfd/libcanbus.so",
            linux_channels={0: (0,), 1: (0,)},  # 两只已确认单通道的适配器
            config=CANFDConfigOptions(config=0x06),  # 不启用适配器内置终端电阻
        ),
    )
)
```

`library_path=None` 沿用 transport 的库查找规则。Linux 未给布局的设备报告 `layout`，
不猜通道。Windows 使用 `HCanbus.dll`，省略 `linux_channels`；DLL 的 `devNum`
直接作为 `device_index`，`channel_index=0`，不按 Linux 的二维索引换算。枚举时需关闭
本进程中使用同一厂商库的连接，否则返回 `busy`。库和原始协议材料不随 SDK 分发。

`total_timeout_s` 默认 30 秒，包含枚举、打开、请求、回复等待；预算耗尽时停止后续
探测并报告 `scan_timeout`。这是协作式预算：无法强行取消阻塞的厂商库/驱动调用，
调用返回后的必要清理也可能超时，不承诺函数必定在 30 秒内返回。不会留下后台扫描
线程继续发送。发送和单次响应仍分别受 `send_timeout_s`、`response_timeout_s` 限制。

## 基本调用

```python
from linkerbot_hand import discover_hands

report = discover_hands()
for candidate in report.candidates:
    print(
        candidate.channel.name,
        candidate.channel.kind,
        candidate.wire_protocol,
        candidate.model,
        candidate.side,
        candidate.status,
        candidate.problems,
    )

candidate = report.require_one()
hand = candidate.create_hand_device()
hand.open()
```

最后三行是三个独立步骤：扫描得到信息、按信息构造关闭状态的设备、显式打开连接。详见下一节。

可以限制扫描接口、L30 Node ID 及 O30 标准帧请求 ID：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands

report = discover_hands(
    DiscoveryConfig(
        channels=("can0", "can1"),
        l30_node_ids=(1, 2, 3),
        o30_request_ids=(0x001, 0x002, 0x123),
    )
)
```

O30 默认只探测 `0x001`、`0x002`；已改地址的设备应显式加入
`o30_request_ids`（`0x000..0x3FE`），不会广播或遍历整个地址空间。
`candidate.create_hand_device()` 保留实际发现的请求/回复 ID，左右手来自身份字段；
请求 ID 本身不能证明侧别。无侧别信息的候选仍需调用方明确指定。

已知 O30 地址时也可直接构造，不必运行发现：

```python
from linkerbot_hand import DeviceConfig, HandDevice

hand = HandDevice(
    model="O30",
    side="left",
    transport=transport,
    config=DeviceConfig(o30_request_id=0x123),
)
```

这里仅配置 SDK 端点，不会写设备 CAN ID；回复 ID 固定为 `request_id | 0x400`。
`o30_request_id=None` 使用对应侧别的默认地址；其他型号不接受该配置。
扩展帧地址和特殊广播 ID 不属于此入口。普通身份刷新保留同一连接的触觉元数据，
重连或识别到身份变化后重新读取。当前实机与最新表格同为 249 字节身份布局，
SDK 按字段内容识别布局，不按固件版本号拒绝连接。

## 返回什么

`discover_hands()` 返回的是一份**信息报告**，不是设备对象。报告只有四样内容：

| 字段 | 内容 |
| --- | --- |
| `report.channels` | 本次枚举并选中的接口；预算耗尽时可能有接口未开始探测 |
| `report.candidates` | 每个应答的端点一条记录：接在哪个 CAN 口、什么型号、左手右手、支持哪些方法；另附序列号、各版本号与内部选路信息 |
| `report.failures` | 接口打不开、发送失败或发现源自身出错；与「设备响应但身份不完整」的候选分开记录 |
| `report.elapsed_s` | 本次扫描耗时 |

`candidates` 里全是不可变数据类，不含任何 `HandDevice`。得到设备对象需要三步，每步都由调用方显式发起：

| 步骤 | 产出 | 是否产生设备 I/O |
| --- | --- | --- |
| `discover_hands()` | 信息报告 | 是，只发身份读取；返回前尝试关闭所有探测 transport，关闭失败另附清理入口 |
| `candidate.create_hand_device()` | **关闭状态**的 `HandDevice` | 否，纯内存构造 |
| `hand.open()` | 已连接、已验证身份的设备 | 是，建连、读身份、读基线 |

三步互相解耦：可以只跑第一步做现场勘查；也可以完全跳过前两步，直接用 `HandDevice(model=..., side=..., transport=...)` 走第三步。发现**不会**替调用方创建对象、打开设备、启动轮询或在多个候选中挑选。

### 单个接口与多个接口

`candidates` 是一个**扁平元组**，跨所有接口拉平，按接口名排序；不按接口分组嵌套。区分来自哪个接口要读 `candidate.channel.name`。

| 场景 | `candidates` | `require_one()` |
| --- | --- | --- |
| 一个接口一只手 | 长度 1 | 返回该候选 |
| 三个接口各一只手 | 长度 3 | 抛 `AmbiguousHandError` |
| 接口正常但无设备应答 | 空 | 抛 `NoHandFoundError` |
| 接口打不开 | 空，原因在 `failures` | 抛 `NoHandFoundError` |

`require_one()` 是**唯一性断言**，不是创建入口：它只在候选恰好一个时返回，绝不静默挑选。
多接口使用时，按已知接线逐个接口扫描并校验唯一候选。下面只构造设备，随后由应用显式
管理 `open()` / `close()`：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands

hands = []
for channel in ("can0", "can1"):
    report = discover_hands(DiscoveryConfig(channels=(channel,)))
    candidate = report.require_one()
    hands.append(candidate.create_hand_device())
```

任一接口无候选、候选不完整或有多个候选时，示例会报错，不覆盖或任选设备。
`verified_candidates` 可用于查看完整身份记录；不完整身份和接口错误分别查看
`report.partial_candidates` 与 `report.failures`。

## 候选里要用的四样信息

一条候选虽然带了很多字段，但正常流程只需要读四样：

| 要知道的事 | 读哪个字段 | 说明 |
| --- | --- | --- |
| 接在哪个 CAN 口 | `candidate.channel.name` | 例如 `can0`、`slcan:COM5`；`wire_protocol` 是已验证的实际线路协议 |
| 什么型号 | `candidate.model` | 公开型号名，例如 `O30`、`L20`；线级工厂前缀已归一 |
| 左手还是右手 | `candidate.side` | `LEFT` / `RIGHT`；L30 不分左右手，为 `NOT_APPLICABLE` |
| 支持哪些方法 | `candidate.capabilities` | **`open()` 之前就能查**，见下 |

`capabilities` 在 VERIFIED 候选上一定存在且 `identity.verified=True`，所以可以在决定要不要连、连了之后能干什么之前就查清楚：

```python
caps = candidate.capabilities
print(caps.joint_names)  # 关节顺序
print([command.semantic.value for command in caps.commands])  # 可写命令
print([channel.semantic.value for channel in caps.telemetry])  # 可读状态
print(caps.operations)  # 清错、使能、标定等
```

其余字段用于记录和排查：序列号、产品编号与各版本号描述设备；`address`、`transport`、
`raw_identity`、`fingerprint` 保留选路及身份依据，`create_hand_device()` 自动使用这些信息。
USB 来源的 `candidate.transport.connection_key` 保存原生通道、速率、串口参数、FD timing
或厂商库路径/索引/配置，不依赖来源对象随后是否再次枚举。构造的 transport 仍为关闭状态。

`channel.supports_classic` / `supports_fd` 描述适配器能力（FD 可为未知），
`channel.probe_protocols` 描述本轮顺序，`candidate.wire_protocol` 描述设备验证结果；
不能仅凭 `channel.kind=CAN_FD` 判断接的是 FD 手。

`candidate.reported_side` 是设备**单独上报的物理侧字段**，与 `candidate.side` 不是同一件事：判断「用哪个 side 建设备」一律看 `side`，`reported_side` 只作为设备自报证据。L30 是两者不一致的典型，说明见下文「已验证、部分识别与失败」。

## 探测顺序

发现不会把全系列都当作 `0xC0` 协议。实际探针如下：

| 型号 | 线路协议 | 最小身份探针 | 地址范围 |
| --- | --- | --- | --- |
| L6、O6、L20Lite、L20 | classic CAN | `0xFF#C0` 产品码；识别型号后读取 `C0/C1/C2/C4` 完整身份 | 广播优先，无回复或缺片时依次回退 `0x27`、`0x28` |
| O20 | CAN FD | DeviceInfo 寄存器 `0x00` | Device ID `1/2` |
| O30 | CAN FD | HOP ProductInfo `MI 0x41`，随后读取完整身份字段 | 每侧一对固定 ID：右手请求 `0x001`、响应 `0x401`；左手请求 `0x002`、响应 `0x402`。两对都探测 |
| L30 | CAN FD | 配置对象 DeviceInfo `0x02`，响应后读取 ProductCode `0x03` | Node ID `1..31`，可配置 |

L20 对外始终报告公共型号 `L20`。发现和身份校验接受常见的 `LHT20-...` 产品编号（例如
`LHT20-10-499-L-B-1-D`），并继续兼容已部署设备上的 `L20-...`、`L25-...`、
`LHL25-...` 和 `LHG20-...` 编号。

O30 对外始终报告公共型号 `O30`。发现和身份校验接受 `O30`、`O30-...`，也兼容历史的
`O30i`、`O30i-...` 产品编号，不区分大小写。L20 与 O30 都在 `product_code` 中原样保留
设备上报的编号，`candidate.model` 和 capability 的 `model` 则始终使用规范型号。

产品编号兼容与配置型号分开：`HandDevice` 和 `get_declared_capabilities()` 的 `model`
应分别填写 `l20`、`o30`（不区分大小写）。历史名称 `l25`、`o30i`、其他工厂前缀及完整
产品编号均不能作为配置值；传入这些值会在设备 I/O 前抛出 `ValidationError`。

已知 nominal bitrate 不是 1 Mbit/s 的接口不会发送 Linkerbot 身份探针；元数据无法提供
bitrate 时则由发现源决定是否允许探测。各通道按 `probe_protocols` 顺序探测；默认策略
见平台表。FD 型号按 O20、O30、L30 顺序查询，一旦获得完整验证的候选就停止该通道的
后续型号/协议探测。部分经典 CAN 身份保留诊断，继续执行策略允许的 FD 探测。
非致命单型号错误不遮蔽后续型号；拔出、失效链路或关闭失败时停止该通道。

只有接口当前配置与型号声明匹配时才发送对应 FD 探针：O20/L30 要求 1 Mbit/s nominal 与 5 Mbit/s data；O30 只要求 1 Mbit/s nominal，它的 profile 不声明 data bitrate（BRS off，数据段恒等于标称速率），因此接口 dbitrate 取任意值都会被探测。发现不为尝试另一协议而重新配置接口。

### 经典 CAN 广播与回退

每轮初筛先发送一条标准数据帧 `0FF#C0`，按设备实际回复的 CAN ID 分别收集产品码。
`0xFF` 是设备协议接受的查询地址，设备通常仍使用自身 `0x27/0x28` 回复。
广播无回复或缺片时依次查询 `0x27#C0`、`0x28#C0`；获得完整产品码后停止回退，
随后在真实地址完成身份验证。未知型号、冲突或不支持的地址作为部分候选保留。

四款均接受片序号 `00/01/02/03` 和字节偏移 `00/06/0C/12`（十六进制），支持乱序及
内容一致的重复片。L6 旧版三片产品码和 L20Lite 18 字节无结束帧格式继续支持；缺失
长产品码末片不会借用另一轮的片段补齐。初筛与完整身份查询的产品码必须一致。
完整身份收齐后仍检查本次响应窗口内的尾帧，出现矛盾版本或产品码时拒绝验证。

各次经典 CAN 查询前排空一个有界的 20 ms 迟到回复窗口，并按对应功能码的发送回执
`claimed_at_s` 忽略接管发送任务前已进入路由的帧；早于整批发送完成的有效快速回复
仍会保留。收包等待使用 `response_timeout_s`，发送等待使用 `send_timeout_s`。经典 CAN
完整身份还保留协议要求的请求间隔，不新增运动或遥测请求。协议没有事务编号，无法
绝对区分隔离窗口之后才到达的同地址旧回复。

当前源码的回执时间过滤、MiniCANFD 与 PCAN/SLCAN 枚举需要配套更新后的 transport 源码。
已发布的 `linkerbot-transport 1.0.0` 在底层发送返回后才记录 claim，可能误丢同步快速
回复，也不包含这些枚举入口。正式发布时须先发布传输层更新，并同步提高 SDK 的最低
依赖版本。源码联调需在同一虚拟环境安装两个仓库的当前源码。

当前经典 CAN 控制使用默认左右手地址。广播收到其他地址时保留真实 ID、产品码及
可解析侧别，返回 `unsupported_address`，拒绝创建设备；不会改地址或静默改用默认地址。

## 安全边界

发现过程只发送身份读取，不发送角度、速度、力矩或电流设置，也不执行清错、使能、失能、
标定、参数保存或遥测轮询。L30 的 Node ID 扫描是多个只读 DeviceInfo 请求，不写入地址。

这里的“只读”是 Linkerbot 协议语义，不代表被动监听：发现会主动发送上述身份帧。如果一个
接口还连接了非 Linkerbot 设备，SDK 无法保证对方不会把相同 CAN ID 和载荷解释为其他含义；
混接或用途不明的总线应通过 `DiscoveryConfig(channels=(...))` 明确限定，不应执行全接口扫描。

SDK 实现当前最新协议，不按固件版本预先禁止发现。旧固件不响应、返回空数据或返回不完整
身份时，结果是无候选或 `PARTIAL` 候选，不会因此修改设备或尝试运动。

## 已验证、部分识别与失败

`DiscoveryStatus.VERIFIED` 表示型号、协议要求的身份和端点选择字段均已验证。候选包含
`device_info`、已验证 `capabilities` 以及实际 `wire_protocol`，可直接调用
`create_hand_device()`。

L30 的能力映射不按左右手分叉，因此 `candidate.side` 为 `NOT_APPLICABLE`；产品身份中验证出的
实际左/右手保存在 `candidate.reported_side`，Node ID 保存在 `candidate.address.node_id`。

`DiscoveryStatus.PARTIAL` 表示总线有响应，但证据不足以安全选择。原因位于
`candidate.problems`，例如：

- `identity_timeout`：最小身份有响应，但完整身份字段超时；
- `product_code_incomplete`：C0 产品码缺片或未结束，回退后仍未收齐；
- `malformed_identity` / `identity_mismatch`：响应结构或字段互相矛盾；
- `unknown_model`：classic 产品码无法映射到公开型号；
- `address_collision`：经典 CAN 同一字节偏移的分片内容冲突，或 FD 身份响应发生重复/冲突；
- `unsupported_address`：经典 CAN 回复地址不能用于当前 SDK 控制；
- `missing_side`：协议没有给出可验证左右手。

O30 会完整返回型号、UID、协议、硬件、固件、机械和传感器身份，并从身份字段解析左右手，因此候选通常是完整的、`side` 已确定，直接创建即可：

```python
candidate = report.require_one()
hand = candidate.create_hand_device()
```

不要写死 `side`：候选已有 `side` 时传入不同值会抛 `ValidationError`，写死 `side="right"` 的代码遇到左手 O30 会直接失败。若确实要显式传，必须与发现结果一致。

只有固件未提供左右手字段时，O30 候选才会带 `missing_side` 问题、`side` 为 `None`，这种情况需要在确认实物后显式传入：

```python
candidate = report.require_one(allow_partial=True)
hand = candidate.create_hand_device(side="left")
```

除仅缺少 `side` 的候选外，部分候选不能创建 `HandDevice`。

无法打开/关闭 transport、发送失败或发现源错误记录在 `report.failures`，与“设备响应但
身份不完整”的候选分开。`report.require_one()` 在无设备、多个候选或唯一候选不完整时分别
抛出 `NoHandFoundError`、`AmbiguousHandError` 或 `IncompleteIdentityError`，不会静默选择。

枚举错误通过 `failure.backend`、`native_channel`、`code` 定位后端和原生通道。
探测关闭失败时 `failure.cleanup` 保留可再次调用的关闭操作；先处理清理失败，再重新
扫描或连接该通道，避免仍有旧连接占用：

```python
for failure in report.failures:
    print(failure.operation, failure.message)
    if failure.cleanup is not None:
        failure.cleanup()  # 仍失败会抛异常；不应忽略后继续打开相同通道
```

`require_one()` 仅检查候选数量和完整性，不替应用处理 `failures`。

CAN 无法证明两个完全相同的节点是否在同一时刻发送了逐位相同的帧。经典 CAN 中内容
一致的重复分片可以由重发造成，因此不单独作为地址冲突证据；冲突内容仍拒绝验证。
总线拓扑应保证每个接口只连接一只手。

## 自定义发现源

默认源是 `PlatformDiscoverySource`，可显式传入 `SocketCANDiscoverySource`、
`PCANDiscoverySource`、`SLCANDiscoverySource` 或 `MiniCANFDDiscoverySource`。
`CompositeDiscoverySource({"pcan": PCANDiscoverySource(), ...})` 用于组合所需来源。
其他适配器可实现公开
`DiscoverySource`：`list_channels()` 只枚举通道，`create_transport()` 按请求的实际线路
协议返回关闭状态 transport。测试也使用同一接口注入内存设备，不需要 ROS 或硬件。

旧自定义来源接口继续有效；新增可选 `enumerate_channels(config)` 方法时，返回
`DiscoveryEnumeration(channels=..., failures=...)`，发现引擎优先使用它以保留后端诊断。
来源只负责通道及连接配置，型号身份校验仍由公共发现引擎完成。

经典 CAN 自定义来源可使用
`from linkerbot_hand.discovery import classic_discovery_route_key`，并将其传给
`CANTransport(..., route_key_of=classic_discovery_route_key)`。它保留默认地址的正常
路由，将其他地址的 C0 回复归入广播收件路由，原帧 CAN ID 和数据不变，避免注册全部
CAN ID。内置经典 CAN 来源均采用该规则；只支持精确默认地址路由的自定义来源仍能
发现默认地址设备，但不会收集其他地址的广播回复。
