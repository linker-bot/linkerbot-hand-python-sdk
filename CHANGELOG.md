# Changelog

本项目遵循语义化版本。正式发布前的开发版本可能继续增加兼容修正。

所有面向用户的功能、行为、接口、依赖或文档变更都必须在同一个 PR 中更新
“未发布”章节。发布时将该章节内容移动到带日期的版本章节，再重新建立空的
“未发布”章节；纯内部重构且不影响使用者时可以不记录。

## 未发布

- **O6 电流接口语义修正：** `0x02` 查询改由 `get_currents()` 返回 `CurrentSample`，
  同步连接基线、`snapshot.currents`、逐关节 `current_raw` 与 `current_hz` 轮询。
  `set_current_limits()` 继续设置上限，写回复保留为 `current_limits` 配置样本。
  `get_current_limits()` 改为 `COMMAND_RESPONSE` 缓存读取，不主动发帧；无有效写回复
  或重连后返回不支持。查询与写回复不互相覆盖，无所属事务的 `0x02` 报文不猜测发布。
  保留原始 count、物理比例冲突说明及既有的回复结构校验，不增加总线请求。

- L6/O6 数值型配置写入统一保留设备实际回复，取消 O6 六类配置及 L6 C5/38 的
  发送值相等限制。完整有效回复进入 `ACKNOWLEDGED` 并更新原始样本，不证明目标已
  原样生效；地址、功能码、帧结构与超时检查保留，不追加查询或重写。
- L20Lite 加速度 `87/88` 与堵转配置 `C5/C6/C7` 同样接受不同于发送值的回复，按
  关节/组号组装设备实际返回值，不再用发送目标构造堵转样本。保持两组完整后原子发布、
  缓存 getter 不发查询、缺帧/格式错误/冲突重复帧保留上一份完整样本。

- L20 `0x59–0x5D` 故障样本新增逐关节 `L20FaultStatus`，提供电机通信异常、软堵转、
  过温、自检异常、电压异常、失速、过流与堵转的具名属性和解释，保留 16 个有效关节的
  原始字节。首次连接、主动查询、轮询与快照统一解码，能力标记 `decoded_statuses=True`。
  明确软堵转抓握时可能置位、松开后自恢复；不增加 CAN 请求或固件门槛，不改变清错契约。

- L20Lite 新增 `set_accelerations()`，通过 `0x87/0x88` 设置两组各五关节的
  0..254 原值；有界 FIFO 排队，两组有效回复收齐后才确认并更新完整样本。
  `get_configured_accelerations()` 仅读取本次连接已确认的回显缓存，不发送主动查询；
  能力标记为 `COMMAND_RESPONSE`，物理比例未知。缺失或错误回显不覆盖上次完整值，
  重连清空缓存且不重放配置；型号文档补充固件 4.6.10 实机回显验证及边界。

- O6 `0x35` 故障样本新增逐关节 `O6FaultStatus`，提供负载过载、过流、过温、
  磁编码异常、过压/欠压的具名属性和解释，保留原始字节与未定义位。
  首次连接、主动查询、轮询和同码报文接收均返回解码状态；能力标记
  `decoded_statuses=True`。不增加 CAN 查询或固件版本门槛，清错仍不支持。

- L6 对齐 V1.0.2：支持 `set_torques()` / `get_torques()`（`0x03`）和
  位置 Kp、位置 Kd、力控 Kd 的原始值读写（`0x06/0x07/0x08`），补齐类型化样本、
  快照、能力与单位/默认值说明。新配置使用有界 FIFO，模式切换实施可中断的等待间隔，
  可选查询继续按设备响应降级，不添加固件版本门槛。`0x08` 使用
  `set/get_torque_kd()`；`0x02` 电流上限和 `0x36` 当前电流保持独立。
  `0x03` 查询返回设备的力矩出力/模式原值，实测力矩语义及物理比例尚未确认；
  可通过 `PollingPlan.torques_hz` 轮询，不声明已校准的扭矩反馈。
- 根据 L6 V2.4.5 实机保留新控制指令回复的旧配置值，主动 getter 读取新值；
  C6/C7 接受设备量化后的配置回复，修正写入生效却因不完全回显被判失败的问题。
  不自动换算或补偿发送值；`0x38` 保留实际回复，并记录实机未确认非原值写入生效。
- L6 故障新增逐关节 `L6FaultStatus`，解释 MCU/电机过温、两类过载及 A/C/B 相过流，
  保留原值和保留位；型号文档与固件支持证据同步到 V1.0.2。

- **破坏性接口迁移：** L20Lite C7 改用 `set/get_stall_hold_currents()`、
  `StallHoldCurrentSample` 和 `stall_protection.hold_current`，删除旧堵转扭矩名称。
  保留两组原始写帧、完整回复组装和仅回复缓存读取，不新增查询或物理换算。

- **破坏性接口迁移：** O30 `0x06` 限制改为 `set/get_torques_limits()`、
  `TorqueLimitSample`、`TORQUES_LIMITS` 和 `snapshot.torques_limits`；实时转矩仍用
  `get_torques()`。`0x04` 的电流及限制独立保留，删除旧 `effort_limits` 名称。

- **破坏性接口迁移：** L20/L20Lite 使用 `set_current_limits()` 写入电流限制，
  `get_currents()` / `snapshot.currents` / `current_hz` 读取实时电流；不提供限制配置回读。
  O20 原限制对象改为 `set/get_current_limits()`，与独立实时电流通道分开；协议字节、范围不变。

- L20Lite 故障样本新增逐关节 `L20LiteFaultStatus`，解释负载过载、过流、过热、
  磁编码异常及过压/欠压，保留原始值与未定义位；明确该型号没有清错命令。

- L20Lite 增加五指按类别触觉查询：`fns=0x20`、`fts=0x21`、`fds=0x22`、
  `px=0x23`，均为单请求/单响应，各自支持字段选择、频率配置和可选查询降级。
  默认 `get_force()` / `force_hz` 包含点阵与法向汇总；仅点阵请使用 `"fn"` /
  `force_fn_hz`。保留原始字节和方向无效码，不套用未经确认的物理比例，也不补发
  `0x28..0x32` 单指查询。能力表和型号文档同步列出支持、单位与历史实机边界。

- L20Lite 新增 C5/C6/C7 堵转阈值、触发时间及堵转后输出扭矩配置；通过
  `set_stall_thresholds()`、`set_stall_detection_times()`、`set_stall_hold_torques()`
  写入十关节原值，两组回显完整一致才确认。对应 getter 返回本次连接已确认的回显，
  不发送无协议依据的主动查询；C6 为 10 ms/count，C7 不误称为电流。

- 全型号触觉统一为 `get_force(*fields, timeout=1.0)`，小写字段为
  `fn/fns/ft/fts/fd/fds/px`，默认读取可用的 `fn + fns`；超时现在使用关键字传入。
  样本字段保留原始值、单位、编码、逐值有效性与独立采集时间；能力查询说明支持情况、
  共享查询与 O30 descriptor 探测状态。O30 单独选择 `fn` 或 `fns` 都执行一次完整
  原生读取并返回两者；矩阵型号不通过求和伪造合力，旧矩阵/descriptor 视图继续保留。
- `PollingPlan` 新增 `force_fn_hz/force_fns_hz/force_ft_hz/force_fts_hz/
  force_fd_hz/force_fds_hz/force_px_hz`；`force_hz` 保留为默认 `fn + fns`
  简写，与显式正数 fn/fns 频率同时设置时报错。共享原生查询取最大频率，独立查询
  分别调度；健康状态区分字段请求频率与物理查询频率，避免重复统计总线消耗。
- 多字段主动读取的字段年龄包含等待后续查询的时间；可选字段被判定不支持时同步
  更新快照状态，保留其他字段的原始值、采集时间和序号。O30 能力中的共享字段列表
  在取得 descriptor 后收敛到设备实际声明的字段。

- 精简 `get_snapshot()` 和样本读取的对象构造：只生成一次快照视图，更新 age/stale
  时复用已经验证的不可变样本数据，避免重复遍历关节和触觉数据。公共构造校验、
  自定义样本子类校验、generation、数据时间及旧快照不可变性保持不变。

- L20Lite 的多帧命令/查询使用协议要求的 1 ms 发送间隔，修正实机在共享的
  0.5 ms 默认间隔下偶发遗漏一片回复并导致整个对象超时的问题。

- 降低 `get_health()` 的开销：在固定 2048 条窗口内增量维护精确延迟分位数，避免每次
  读取都复制和排序历史记录；公共接口、分位数定义、错误计数和数据年龄语义保持不变。

- 降低高频状态读取的开销：复用不可变关节投影，内部单通道读取只构造对应样本。
  公共接口与 `get_angles()` 主动查询语义不变；快照仍为零 I/O，数据有效性和
  `age/stale` 每次读取时更新，旧快照不受后续更新影响。

- O30 角度写入的 RTS=0 回复现在发布为当前角度（`DEVICE_COMMAND_FEEDBACK`），
  更新样本 sequence/时间，并声明 `returns_current_state=True`，支持现有三种角度反馈策略。
  保留 ACK 与机械到位的区别，其他配置写回包不作为当前角度发布。

- 修正 L6 触觉文档：每指收齐后可让步给受限数量的角度命令或查询，随后继续读取下一指；
  五指完整样本原子发布不代表同时物理采样。运行时行为不变。

- L20/L20Lite 的五指触觉改为每指 12 行收齐后再查询下一指，指间允许有界调度完整的
  多帧角度命令和查询。五指仍作为一个完整样本发布；缺行或冲突不发布部分矩阵。
  L20 实机的四字节 FF 尾帧不再干扰下一手指，补连续触觉/角度读取回归。

- 补齐限位补偿的原始值、协议 ACK 和共享有界 FIFO 说明，明确队列满时的
  `QueueFullError`，以及已接纳配置不会被同类新值替代。

- 修正 O30 触觉的 255 字节分页（共 765 字节），支持 descriptor 的 `Rsv` 保留字段和
  UTF-8 元数据。明确缺失的手指保留空数据与 `available=False`，其余手指仍正常采集；
  全部缺失报告不支持，畸形响应仍报错，五指合力接口不为缺失手指填零。
- O30 HOP 通信错误按位解释并报告 `ProtocolError`，不再冒充机械故障；仅明确的
  可选对象缺失报告固件不支持。与请求重叠的越界分片立即报错，无关范围继续忽略。

- **破坏性配置变更：** `HandDevice` 和 `get_declared_capabilities()` 不再接受
  `O30i/o30i` 型号别名，请将 `model` 改为 `O30/o30`。这与 L20 拒绝 `L25/l25`
  配置值的策略一致；设备上报的旧 `O30i`、`O30i-...` 和 L20 历史产品编号仍可发现、
  校验并以原值保留在 `product_code` 中。文档补齐 `l20-lite` → `L20Lite` 拼写别名。

- O30 标准单播地址限制为 `0x000..0x3FE`，发现支持显式 `o30_request_ids`，
  候选工厂保留发现的实际端点；可用 `DeviceConfig(o30_request_id=...)`
  直接连接已改地址的设备，不修改设备 ID，也不从地址推断侧别。
- O30 身份刷新保留同代次触觉元数据，重连/身份变化时失效；身份文本采用 UTF-8，
  已读取的编译时间前缀不再重复查询，布局识别不依赖协议版本号。

- **O6 触觉通道不再按出厂编码门控。** 原先只有身份确认为 `04-B` 的手才声明 FORCE 通道，其余变体在连接时就被静默剔除。这个门槛从根上是错的：`02-B`、`03-B`、`05-B` 等变体同样可能带触觉硬件，`B` 只是传感器标识而非能力开关，而出厂编码本身也不描述点阵布局——SDK 组装的 `10x4` 是写在事务里的，与编码无关，所以这道门槛什么都没保证，只会漏掉真有触觉的手。现在所有 O6 都声明该通道，实际可用性交给既有的固件可选降级判定：连续 3 次超时、空响应或结构不符后该通道被标记为不支持，getter 抛 `UnsupportedFirmwareError`，连接与其他能力不受影响。这也与本仓一贯做法一致——L6 与 O30 的触觉本来就只靠运行时降级，O6 是唯一的例外。**判断一只手是否真有触觉请用 `capabilities.telemetry_supported(TelemetrySemantic.FORCE)`**（或看 `firmware_unsupported_telemetry`）：不能再依赖「非 `04-B` 的手连接后 `telemetry_channel(FORCE)` 立即返回 `None`」，该通道现在始终有声明，降级后也保留元数据、只是 `telemetry_supported()` 转为 `False`。点阵不是 `10x4` 的手会因长度不符走同一条降级路径，不会被错误解析，相应报错现在会指明 SDK 组装的布局。

- 出厂编码超出 SDK 可组装的四个分片时，报错改为说明这是 SDK 的 24 字节上限，不再读起来像设备违反协议；序列号的终止分片以最早出现者为界。

- 修正 L20Lite 完整的 18 字节出厂编码没有终止帧时连接/发现超时的问题；
  接受三片内完整的七字段编码，同时保留缺片、重复冲突和型号/侧别校验。

- 修正多阶段读的 interstage 角度工作在连接失败就地断连、结束当前连接代次后，外层事务仍向已结束代次发帧、并再断连一次冲掉已保存 polling plan 的缺陷。

- L20/L20Lite 的速度、温度、触觉查询接入运行时可选查询判定：本次连接尚未成功读取的
  通道连续三次超时或格式不符后停止查询并报告 `UnsupportedFirmwareError`，其他通道
  与连接保持可用；成功读取过的通道不会因后续丢帧误报固件不支持，重连后重新判定。
  身份和角度、故障、负载基线仍需成功读取，不按固件版本号限制连接。

- CAN FD 发现按型号隔离 O20/L30/O30 探针异常，单个探针失败会留下诊断并继续后续型号；
  已关闭或失效的链路停止发送。classic 响应抑制同通道 FD 探测的策略保持不变。

- 修正隔离区（quarantine）中滞留事务解析迟到帧抛错时被错误归因到当前在途事务的缺陷；现丢弃该隔离条目并按其自身错误上报，不再污染在途事务或阻塞同关联键的后续事务。

- 修正 `get_health().consecutive_errors` 被「请求刚发出」就清零的缺陷，它让 SDK 无法呈现最常见的一类现场故障。`RuntimeMetrics.sent()` 原先在 transport 把请求发出的那一刻就把连续错误数归零，但「已发出」不等于「设备回应了」—— 真正代表逻辑成功的是完整事务收口（`transaction_completed()`）。后果是「发送成功、等响应超时」这一形态被完全掩盖：连续三次这样的失败，计数序列是 `[1, 1, 1]` 而不是 `[1, 2, 3]`，因为每次新请求发出就把上一次超时累计的错误抹掉了。而这恰恰是 CAN 设备掉线、线路单向异常、固件不支持某项查询时的典型形态 —— socket 仍然可写，所以发送始终「成功」，设备却再也不回帧。已用 `tests/hand/api_v1/runtime/test_device_delivery.py` 的新增回归测试确定性复现：修复前第二次超时后计数仍为 1。对下游的实际影响是 ROS 侧 `consecutive_errors >= 3` 的 `repeated SDK I/O errors` 这条 ERROR 判定**从来没有可达过**，设备持续不响应时现场只能看到 WARN「缓存角度数据过期」，把持续性硬件故障显示成数据过期。现在 `sent()` 只更新 `last_successful_send_at`，连续错误数仅在完整事务成功或新代次身份验证成功（`ready()`）时归零。该字段仍是可增可减的 gauge 而非单调计数器，健康运行收尾时依旧为 0，依赖这一点的 ROS benchmark 与硬件验收脚本不受影响。使用者可能观察到的变化是：真实持续故障下这个值现在会如实累加，ROS 诊断相应从 WARN 升级到 ERROR。同一处语义问题的另两种形态一并处理。ACK 失配（设备回帧但不确认命令）无需改动：它在 `_transaction_runner.py:363` 抛错，位置就在事务收口清零之前，本来就能如实累加 —— 探针实测为 `[1, 2, 3]`；`_device_actor.py` 里那句 `device response did not acknowledge command` 经推导与实测均不可达，是防御性断言。第三种形态确实需要修：读事务的响应被消费者判为完整、却没有产出请求的那个数据（典型成因是 multipart 响应丢片、协议字段错位、型号能力声明与固件实际不符），原先由 actor 在 `runner.execute()` 返回**之后**才校验，而清零已经发生，实测为 `[1, 1, 1]`。现在 `TransactionRunner.complete()` 接受 `require_published_semantic`，把「调用方确实拿到了它要的数据」纳入事务本身的成功条件、在收口清零之前判定失败；读路径传入所请求的 semantic，actor 原来那道校验退化为不可达的防御性断言。没有采用「把清零上提到 actor 的命令、读、轮询三条成功路径」这一方案 —— 漏掉任何一条都会让计数永不归零、把健康设备打成永久告警，而 runner 只有一处收口，风险小得多。

- 修正 `scripts/release.py` 的两处缺陷，都是随发布流程一起引入的。**一、`publish` 不校验构建的源码是否来自标签。** 它为适配 CI 的 detached tag checkout，把工作区与标签两道门禁整个跳过了，后果是本地跑 `publish` 时没有任何门禁把构建的源码与标签关联起来。已实测复现：`HEAD` 领先 `v2.0.0` 一个提交、且 `src/linkerbot_hand/__init__.py` 有未提交改动时，四道门禁全绿放行，会上传一份在 git 任何提交中都不存在的源码。registry 的版本不能覆盖，所以不可挽回 —— 日后 checkout 该标签想复现或审计这个包，拿到的是另一份源码，而这恰恰是标签存在的唯一意义。现在 `check_working_tree(require_main=...)` 只在 `publish` 时去掉「必须在 `main` 上」这一半、保留「已跟踪文件无改动」；并新增 `check_tag_points_at_head()`，`publish` 用它替代 `check_tag_absent()`，要求 `v<版本>` 存在且正好指向 HEAD。`release.yml` 的 checkout 显式加了 `ref: ${{ github.ref }}`，使该门禁在 CI 中确定成立；本地则被迫先 `git checkout v<版本>`，发布顺序相应变为 `tag` → `checkout 标签` → `publish`。**二、缺 Gitea Release。** 脚本把版本号、CHANGELOG、README、标签、registry 上传五件事做成了门禁，却没覆盖 Release 对象 —— 而 2.0.0 唯一漏掉的恰恰是它：`v2.0.0` 一度是本仓 9 个标签中唯一没有 Release 的一个，因为没有任何门禁让它显形。`publish` 现在在上传后创建 Release，标题 `linkerbot-hand <版本>`、正文取自 CHANGELOG 的对应章节、附 wheel 与 sdist（与本仓 8 个历史 Release 的惯例一致），已存在时跳过而非报错以保持幂等。根因是当初照着「上一次哪里出错」定门禁清单，而不是照着「本仓一次完整发布实际由什么构成」。`twine` 加入 dev 依赖组 —— 发布路径本来就依赖它却未声明。同时删去上一条里「发布范围不含 `linkerbot-hand-ros`」的说法：那个判断有误，registry 里 ROS 实际有 `0.3.1` 到 `2.0.0` 共 6 个版本，历史上每次发布都上传了，其 registry 上传与 Release 由 ROS 仓库的 #97 补齐。

- 新增发布流程：`scripts/release.py`（`check` / `tag` / `publish` 三个子命令）与 `.gitea/workflows/release.yml`（推送 `v*` 标签时触发），流程说明见 [scripts/README.md](scripts/README.md)。发布一个版本需要五处保持一致 —— `pyproject.toml` 的版本号、`CHANGELOG.md` 的带日期章节、两个 README 里钉住的版本、git 签名标签、registry 里的产物 —— 而 2.0.0 那次只做到前两处，结果是版本发了却没人装得上（文档仍指向 `1.0.0`、没有 `v2.0.0` 标签、registry 里只有 `1.0.0`，ROS 声明的 `linkerbot-hand>=2.0.0` 无法解析）；补救过程本身又在文档修正尚未合并时打了标签，只能删掉远端标签重做。脚本把这些做成机械门禁：工作区与分支状态、CHANGELOG 有带日期章节且 `未发布` 已清空、两个 README 恰好钉当前版本且无残留旧版本、标签未被占用、`ruff` 与 `pytest` 通过、该版本尚未发布过。`publish` 另外校验产物本身而不只是「构建成功」：`twine check`、wheel 元数据版本、wheel 长描述里钉住的版本（`readme` 会把 README 打进产物，钉错就永久留在已发布的包里）、以及 sdist 内不含 git 未跟踪的文件。registry 不可达时明确报「未校验」而不是当作「没发布过」，否则一次网络故障就会放过一次重复上传。分工是「打标签是人的动作，上传是自动化的动作」：本地 `tag`，CI 在标签推送时 `publish`，且 `publish` 幂等（registry 已有该版本则跳过），因此本地与 CI 各跑一次不会重复上传。Gitea runner 当前离线，工作流交付后只会排队不执行，现阶段两步都在本地跑。

- `ruff check` 与 `ruff format --check` 的 CI 路径从 `src tests` 扩展为 `src tests scripts`。pre-commit 钩子按文件名传参、本就会检查 `scripts/`，而 CI 按目录列举则不会 —— 新增该目录后两者会对不齐。

- 修正两个 README 的安装说明。快速启动仍钉在 `1.0.0`（venv 目录名、`==` 版本、预期输出共 4 处），照文档执行会装到旧版本。源码安装原先只有一行 `pip install -e .`，实测在干净环境失败 —— `linkerbot-transport` 不在公共 PyPI，只装 hand 无法解析该依赖。现给出两种实测可行的写法：同时 editable 安装 transport 与 hand，或只 editable 安装 hand 并提供 Gitea 索引以取用已发布的 transport；并附验证命令确认装到的是本地源码而非已发布包。

- 新增 O30 型号接口与原始单位说明，区分 20 公共通道、25 帧槽和 36 协议槽；
  补齐 descriptor 触觉、设备合力、能力参考和验证边界，不把协议原值误写成物理单位。

- 补齐 L20/L20Lite 接口、原始单位、故障位、拆帧与固件实机记录；明确温度为
  1 ℃/count，触觉物理标定未知，速度/负载不作推测换算。记录高负载下仍未解决的
  L20Lite 触觉缺行，不将短时频率写成稳定上限。

## 2.0.0 - 2026-09-11

本版本包含 5 项破坏性改动。逐条摘要如下，受影响的调用方按此调整即可，不必通读全部条目。

| 影响 | 原来 | 现在 |
| --- | --- | --- |
| 公共型号名 `L25` | `HandDevice(model="l25", ...)` | `HandDevice(model="l20", ...)`；不再接受 `l25`。设备身份帧中的历史工厂前缀 `L25`、`LHL25`、`LHG20` 仍可识别 |
| 公共型号名 `O30i` | `capabilities.model` 可能返回 `O30i` | 一律返回 `O30`；`o30i`/`O30i` 仍可作为**输入**别名 |
| **L30 电流限制单位** | `raw_unit == "mA"`，写 `500` 被理解为 500 mA | `raw_unit == "count"` + `raw_scale_note` 为 `6.5 mA/count`。**同一物理值需写 `77` 而非 `500`**；按旧语义传值会得到约 6.5 倍的实际电流限制 |
| L20Lite/L20 速度读取 | `get_configured_speeds()` 可用，`get_speeds()` 返回 unsupported | 互换：`get_speeds()` 可用并返回实测速度，`get_configured_speeds()` 返回 unsupported（协议不提供读回设定值）。ROS 相应发布 `~/state/measured_speeds` 而不再发布 `~/state/configured_speeds` |
| `get_normal_force_resultant()` 支持性判定 | 按型号名 `"O30"` 判断 | 按 force 通道的 `payload_projection` 是否为 `TACTILE_DESCRIPTOR` 判断。对 O30 行为不变；依赖「按型号」这一语义的第三方代码受影响 |


- 统一 O30 数据段速率的文档口径为「**推荐 5 Mbit/s，但任意数据段速率都可用**」。O30 使用 BRS off，数据段恒等于标称速率、接口 dbitrate 不影响通信，此前中文文档只写「任取」而未给推荐值，读者无从判断该配多少；`README.en.md` 更是仍写着过时的「O30 uses 1 Mbit/s for both nominal and data rates」。`README.md`、`README.en.md`、`canfd.md`（能力表与示例）、`hand-discovery.md` 六处已统一，并把「数据段恒为 1 Mbit」这一易误读的表述改为「恒等于标称速率」。

- 新增 CI 与本地 pre-commit 门禁。`.gitea/workflows/ci.yml` 在 push 到 `main` 与 pull request 时执行 `ruff check` 与 `ruff format --check`；pull request 另有一道检查：改动了 `src/` 而未更新 `CHANGELOG.md` 即失败，纯内部重构可在 PR 标题加 `[skip changelog]` 跳过。`.pre-commit-config.yaml` 用 `repo: local` 调用环境中的 ruff（而非拉取自带独立版本的 `ruff-pre-commit`），使钩子、手动执行与 CI 三处规则与版本一致；每个克隆需各自执行一次 `pre-commit install`，它会同时装上 commit 时与 **push 时**两种钩子；在 Gitea runner 上线之前，push 钩子是实际起作用的那道门禁（无 runner 时工作流只会排队不执行）。`pre-commit` 已加入 dev 依赖组。CI 暂不执行 pytest —— `linkerbot-transport` 不在公共索引且仓库私有，需先确定依赖分发方式。

- `TelemetryCapability` 新增 `payload_projection`（枚举 `SampleProjection`：`RAW_VECTOR` / `TACTILE_MATRIX` / `TACTILE_DESCRIPTOR`），声明该通道的样本会填充哪些字段。七个型号的 force 通道各自声明（六个矩阵、O30 描述符），其余通道为默认 `RAW_VECTOR`。此前消费方要判断「force 是标量、矩阵还是描述符」只能解析 `raw_encoding` 字符串并数 `shape` 的维数，而这两者描述的是线上格式、与样本填哪个字段逻辑上独立：O30 的描述符 force 恰好也是一维，靠 `shape` 本身分不开标量与描述符。
- `get_normal_force_resultant()` 改为按 force 通道的 `payload_projection` 判断是否支持，不再硬编码型号名 `"O30"`。将来第二个型号支持描述符触觉时无需改动该方法。

- `TelemetryCapability` 新增 `decoded_statuses`，声明该通道的样本是否附带已解码的逐通道状态对象。O30 的故障通道声明为 `True`，其余型号所有通道为 `False`（默认值，现有声明未变）。此前消费方要在 `open()` 之前知道「这个通道会不会带解码结果」，只能去猜 `raw_encoding` 字符串 —— 而 `raw_encoding` 描述的是线上字节布局，与「SDK 是否替调用方解码」逻辑上独立，今天只是恰好一一对应，将来别的型号接入解码但编码不同就会被漏掉。新增契约测试固化 capability 声明与 `protocols/o30/telemetry.py` 读对象表的一致性，防止两张表漂移。

- 接通 O30 的关节故障位解码。`decode_fault_statuses()` 早已按 0.0.4 协议实现完整的 7 位解码，但从未被生产代码调用，因此 `get_faults().statuses` 对 O30 恒为空元组，`ad47d9c` 引入的能力对外完全不可见。现在读对象描述表可声明解码器，故障对象（MI `0x0C`）据此在样本中填入逐关节 `JointFaultStatus`：使用者可直接判断 `statuses[i].over_temperature`，不必自行对 `raw[i]` 做位运算并查协议。查询方式不变，仍是一帧读回全部 20 个关节；`raw` 原样保留。**位含义依据 0.0.4 协议帧格式表，未经实机故障场景验证** —— 要确认需让设备真实堵转、过温或掉线。

- 修正 L20Lite 与 L20 把实时测量值错标成配置值的状态语义。协议规定这两个型号的速度与转矩写入携带目标值并被回显，而只发功能码的查询返回**实时值**；SDK 此前把速度查询映射为 `CONFIGURED_SPEEDS`、并把速度与 `EFFORT_LIMITS` 都列入配置响应集合、声明 `device_measured=False`，等于把设备实际的运动与负载当成「你刚写进去的配置」。现在速度通道改为 `MEASURED_SPEEDS`，两个通道都声明 `device_measured=True`，读取结果的 `DataOrigin` 由 `DEVICE_CONFIG_RESPONSE` 改为 `DEVICE_RESPONSE`。**行为影响**：这两个型号的 `get_speeds()` 开始可用并返回实测速度，`get_configured_speeds()` 转为 unsupported（协议不提供读回设定值）；ROS 相应发布 `~/state/measured_speeds` 而不再发布 `~/state/configured_speeds`。`EFFORT_LIMITS` 暂不改名——协议文字在电流与转矩之间混用，最终命名待实机确认，当前只在 `raw_scale_note` 中写明该查询返回实时值。

- 修正 L30 电流限制的单位声明。`CommandSemantic.CURRENT_LIMITS` 与 `TelemetrySemantic.CURRENT_LIMITS` 原先声明 `raw_unit="mA"` 且 `unit_confidence=MEASURED`，而协议原文是「范围 60～800，单位 6.5ma」，即原始值是 count。两处改为 `raw_unit="count"` + `raw_scale_note="6.5 mA/count is documented but not converted by the SDK"` + `unit_confidence=DOCUMENTED`，与同型号已正确声明的实测电流通道一致。**行为影响**：`raw_range` 不变（仍是协议的 `60..800` count），SDK 仍然只返回协议原始值、不做任何换算；但依赖 `raw_unit` 把该数值当作 mA 使用的调用方需要改为按 `6.5 mA/count` 自行换算——此前按 mA 理解会产生 6.5 倍偏差。同步修正 `hand-capabilities.md` 中「协议值本身以 mA 表示」的说法。

- 纠正并补全自动发现文档。修正描述错误：发现文档与两个 README 不再声称「O30 无法可靠验证左右手、候选保持 `side=None`」（身份字段已给出左右手，候选的 `side` 已确定，写死不一致的 `side` 会抛 `ValidationError`）；O30 探测地址补上左手 `0x002`/`0x402` 与「两个端点都探测」；FD 探针的 data bitrate 口径改为 O30 只要求 1 Mbit/s nominal、接口 dbitrate 任取。新增说明：`discover_hands()` 返回的是信息报告而非设备对象，以及「扫描 → 构造关闭设备 → `open()`」三段职责边界；单接口与多接口下 `candidates` 的形状和 `require_one()` 的行为；候选里正常流程真正要用的四样信息（CAN 口、型号、左右手、支持的方法），并说明 `capabilities` 在 `open()` 之前即可查询。
- 给 actor 主循环补异常边界。此前 `_main_loop` 位于 `else:` 分支、完全没有异常边界，初始化成功之后的未捕获异常会让 actor 线程带 traceback 静默退出，而 `open()` 早已返回成功、`get_health()` 仍报 `CONNECTED`、`auto_reconnect` 永不触发、`recover()` 因线程已停止抛 `ClosedError`，设备只能 `close()`。触发链是可选查询连续超时期间另一线程调用 `close()`，使降级逻辑从 `except` 处理器内部抛出 `DeviceNotReadyError`。现在线程异常会被分类、上报并置位停止标志，健康状态如实反映故障。

- 修复总线杂散报文可让无关事务失败、并把正常通道永久降级的问题（影响 L6 与 O6）。`protocols/base.py` 规定 `decode_report` 未匹配时返回空元组，但 `decode_u8_vector` 在首字节撞号、长度不符时会抛 `ProtocolError`。后果：一帧与遥测 opcode 撞号的杂散帧会让当时在飞的无关事务以错误的异常类型和无关的 opcode 文本失败（例如 `0xC6` 写入报出 `opcode 0x01 response must contain 7 bytes`，本应是 `TIMEOUT`）；连续三帧还能让一个每次都正常应答的查询通道被判为「固件不支持」而永久停用。现在报文解码路径按契约返回未匹配，主动响应路径仍严格校验长度。

- 放宽 O30 的数据段波特率约束。O30 使用 CAN FD 且 BRS off，数据段速率恒等于标称 1 Mbit，接口配置的 dbitrate 实际不影响通信（1M/5M/7M 均可）。此前 capability 把 `data_bitrate` 声明为 1M，导致自动发现要求接口数据段速率恰好 1M —— 接口配成实机常用的 5M 时 O30 扫不到。现在 `data_bitrate` 声明为 `None`，发现在型号未声明数据段速率时接受任意接口 dbitrate（仍按标称 1M 匹配）。O30 因此可直接接在 O20/L30 所用的 1M/5M 总线上。

- 自动发现的 O30 探测改为同时探测左右手两套 CAN ID 端点（右 `0x001`/`0x401`、左 `0x002`/`0x402`）。此前只发右手请求、只听右手响应，总线上的左手 O30 扫不到。左右手同时在线时会返回两个候选，`require_one()` 相应抛 `AmbiguousHandError`。

- O30 按左右手选择 CAN ID。O30 出厂约定右手用请求 `0x001`/响应 `0x401`、左手用 `0x002`/`0x402`（双手共总线时靠不同 ID 区分）。此前 CAN ID 写死且与 `side` 无关，`HandDevice(model="O30", side="left")` 连不上左手。现在 backend 依声明的 `side` 计算端点与响应路由，四类事务全链透传；HOP 对象、帧布局、字节序与关节映射不变。

- 补齐 O30 的电流限制读写与力矩实测读。O30 0.0.4 的电流（`0x04`）与转矩（`0x06`）各有「实测/设定」两个视图。新增 `get_torques()` 读实测转矩、`get_current_limits()` 与 `set_current_limits()` 读写电流限制。写「限制/目标」必须走 RTS=1（RTS=0 带数据会被设备当作读实测而不写入），该行为已实机验证。

- 新增 O30 HOP 0.0.4 的描述符驱动触觉传感器支持与法向力合力读取。包含 0.0.4 的身份布局（实机与文档两种）、协议版本识别、故障位解析、wire_slot 与 logical_joint_id 两套关节索引，以及新增公开接口 `get_normal_force_resultant()`（五指法向力合力）与 `ForceSample.sensors` 等触觉描述符类型。运行时新增「下一事务阶段依据上一响应内容动态选择」的链式事务能力。**不提供 0.0.3 旧协议的降级兼容。**

- 将公共型号 `O30i` 统一为 `O30`（**破坏性**）。capability、协议 backend、registry、自动发现、测试与中英文说明中的公共型号统一为 `O30`，SDK 对外始终返回规范型号 `O30`；`O30i`/`o30i` 仍可作为配置输入别名。设备返回的 `O30`、`O30-...`、`O30i`、`O30i-...` 产品号均兼容，完整产品号保留在身份信息中。HOP 对象、CAN ID、帧布局、字节序、传输参数与 20 关节映射不变。

- L20 自动发现和身份校验新增常见 `LHT20-...` 产品编号前缀兼容；例如
  `LHT20-10-499-L-B-1-D` 会保留原始产品编号，并统一报告公共型号 `L20`。
- 将误命名的公共型号 `L25/l25` 更正为 `L20/l20`；模块、capability、自动发现、
  测试 marker 和协议 profile 同步更名，不再接受 `l25` 作为公共配置值。设备身份帧中的
  历史工厂前缀 `L25`、`LHL25` 和 `LHG20` 仍可识别，并统一报告公共型号 `L20`。
- 新增中英文快速启动流程，显式展示 `linkerbot-transport -> linkerbot-hand` 依赖链、
  隔离环境安装命令和不产生设备 I/O 的安装后验证。

## 1.0.0 - 2026-09-01

- 新增显式 `discover_hands()`，只读扫描 active SocketCAN 通道并识别 L6、O6、L20Lite、
  L20、O20、L30 和 O30；区分网卡模式与实际线路协议，CAN FD 通道优先探测 classic，
  且不修改接口配置。
- 新增已验证/部分候选、通道失败、稳定发现错误和可注入 `DiscoverySource`；多设备、同地址
  冲突、非法身份及 O30 左右手缺失均不静默选择，L30 支持发现并继续使用非默认 Node ID。
- 对照《LHO6 协议文档》补全 O6 `C5/C6/C7` 堵转配置读写与 `CF` 参数保存，
  并将 `0x02/0x05/0x87` 配置写入改为等待设备精确回显确认。
- 补全 O6 电流限制、速度、加速度、温度、故障和 `04-B` 触觉的原始单位、比例冲突与
  解释 metadata；新增逐型号接口和固件支持证据文档。
- O6 的可选配置、温度、触觉和堵转查询按当前连接代单功能降级；查询缺失不会断开
  其他功能，写命令超时仍明确失败；已经验证可用的能力不会因后续单次超时被永久降级。
- L6 旧固件允许在缺少可选能力响应时完成身份连接；L6、O6 的连接能力由固件实测证据
  描述，不再因可选查询无响应阻塞全部功能。
- 将“角度设置返回当前角度”建模为全系列统一事件；各协议 profile 通过
  `returns_current_state` 明确声明并独立处理 CAN/CAN FD 标识、单帧/多帧关联和 ACK，
  公共层不假设 O6 帧格式，也不把无应答、目标值回显或 ACK 冒充当前状态。
- `PollingPlan.angle_feedback` 新增默认 `ADAPTIVE`、`QUERY_ONLY` 和
  `COMMAND_FEEDBACK_ONLY` 三种角度反馈策略；自适应模式只对命令反馈采样缺口主动查询。
- 分离“事务等待响应”和“设备协议 ACK”契约，不支持 ACK 的命令即使等待状态回显，
  成功终态仍为 `DISPATCHED`。
- angle health 增加命令反馈、有效反馈样本和反馈策略指标，显式 `get_angles()` 始终保持
  主动查询语义。
- 轮询频率不再由 capability 人为截断，实际稳定上限由用户配置、协议事务耗时与总线负载
  共同决定。
- 优化单 I/O actor 的命令、轮询和多帧触觉事务调度，减少高频角度收发与触觉读取之间的
  饥饿和无效等待。
- `get_snapshot()` 继续保持纯缓存非阻塞语义；新增按缓存 revision 等待更新的独立接口，
  供 ROS 发布线程在不主动查询设备的前提下等待新样本。
- 运行依赖升级为 `linkerbot-transport` 1.0.0，使用正式化的共享连接、精确接收路由和
  有界发送仲裁契约。

## 1.0.0.dev0 - 2026-08-28

- 删除 `linkerbot.hand` 七型号类、manager、`HandSession`、legacy backend 和重复
  runtime，只保留 `linkerbot_hand.HandDevice` 一种公开调用方式。
- 普通 `set_*()` / `get_*()` 统一表示协议原值；百分比接口统一使用
  `set_percent_*()` 命名，删除 LUT、标准 raw、hardware raw 和 `submit_*` 别名。
- capability registry 只返回 Hand 1.0 不可变类型，不再生成旧 contract 兼容视图。
- 使用单 actor runtime 统一生命周期、命令、主动读取、轮询、缓存和安全恢复；同一物理
  CAN/CAN FD 通道只保留一个连接、RX owner 和有界 TX arbiter。
- 将 L6、O6、L20Lite、L20、O20、L30 和 O30 全部迁移到独立协议 profile，并由
  capability 表达型号、关节、传感器、命令和反馈差异。
- health 区分逻辑读取完成、缓存更新来源与 ROS 发布所需指标；transport 流量统一按共享
  物理通道累计帧数和 DLC 数据区字节数。
- 核心发行依赖移除 NumPy，并将 transport 依赖限制为 CAN/CAN FD extras；wheel 不再包含
  `linkerbot` namespace，sdist 不再携带内部测试。
- 重写 README、API reference 和破坏性迁移说明，删除只能调用旧 manager 的型号文档。

## 0.2.1 - 2026-08-26

- 解析 O6 `0xC0` 出厂编码中的产品版本、左右手和传感器类型；
  `LHO6-04.1-730-R-B-1-D` 识别为右手 `04-B` 变体。
- 明确 O6 `04-B` 使用 `B1..B5 A4` 查询五指 `10x4` 点阵触觉，并在统一
  capability 中只对已实现的 04 系列传感器变体声明触觉支持。
- 连接和安全恢复后均重新应用设备身份对应的 capability，ROS 继续直接消费 SDK
  能力描述，无需维护独立型号规则。

## 0.2.0 - 2026-08-26

- 将 `set_angles()` / `submit_angles()`、`set_speeds()` /
  `submit_speeds()` 统一为协议原始值接口；命令值不再隐式归一化或换算物理单位。
- 新增明确命名的 `set_percentage_angles()` / `submit_percentage_angles()`；L6、O6、
  L20Lite 和 L20 还支持显式百分比速度接口。
  <br>**后续变更（加注，非勘误）**：上述名称已不再存在。百分比角度接口现命名为
  `set_percent_angles()`；`submit_*` 系列在「收敛为唯一 `HandDevice` 内核」时整体移除；
  百分比速度接口现为 `set_percent_speeds()`，且因七个型号都没有 profile 声明速度的百分比
  映射，当前对所有型号返回 unsupported。按本条目查找方法名会找不到。
- `SensorSample.values` 只包含协议解码值，并补充 `unit`、`encoding`、`physical_unit`、
  `scale`、`offset`、`value_kind` 与 `matrix_shape`；删除重复的原始值通道，
  `JointState.angle` 也直接保存协议位置。
- 删除用户角度映射实现、持久化依赖、旧 raw-angle 方法和兼容别名。本仓库不兼容旧映射
  行为。
- 将 RPM、mA 和物理角度转换保留为显式纯函数；O20 的 `0..100` 速度仍按协议范围处理，
  不声明为百分比。
- 基于完整协议资料补全 L6、O6、L20Lite、L20、L30、O20、O30 的原始编码、物理单位、
  已确认比例/偏移、故障值语义和触觉矩阵尺寸；资料冲突时不发布推测比例。
- 修正 L30 反馈编码为大端 `i16_be`，保留经典手故障字节的保留位，并让协议 `u8`
  温度保持整数类型。
- 修正 O30 方向轴的百分比换算与关节物理语义，并将 `0x20` 高分辨率位置按协议定义的
  无符号小端 `u16_le` 解码；主反馈继续返回 `0..255 count`，仅运动时间发布
  `10 ms/count` 的已确认比例。
- 扩展 `.gitignore`，覆盖常见 Python 构建、测试、类型检查、虚拟环境、编辑器和本地配置
  产物，并忽略本地 `superpowerdocs/` 协议资料目录。

## 0.1.3 - 2026-08-25

- L6、O6、L20Lite 和 L20 的多帧序列号读取同时兼容连续帧号
  `0、1、2、3` 与字节偏移 `0、6、12、18` 两种位置编码。
- 兼容 L6 V2.3.3 返回三个连续满帧且不携带终止符的序列号响应。
- 严格拒绝缺失中间帧、非法位置和旧事务残片，避免使用不完整或过期数据完成
  设备身份验证。

## 0.1.2 - 2026-08-25

- 修正无依据的 `numpy>=2.2.6` 下限，Python 3.10-3.12 支持 NumPy 1.21.6 至 1.x，
  Python 3.13 及以上支持 NumPy 2.1 至 2.x。
- ROS 2 Jazzy 的 Python 3.12 环境默认解析到 NumPy 1.26.x，避免与系统 SciPy 1.11.4
  的 `numpy<1.28` 约束冲突。
- 增加依赖标记回归测试，并在 NumPy 1.x 和 2.x 环境中验证相同 SDK 功能。

## 0.1.1 - 2026-08-25

- L6 的力矩限制统一使用 mA，`0–255` 协议值映射为约 `0–1400 mA`；速度仅保存已设置目标，不伪造当前速度反馈。
- O6 的速度、力矩限制和加速度分别统一为 RPM、mA 和度/秒²，并保留协议量化转换。
- L30 的扭矩设置明确为电流上限并使用 mA，当前电流反馈统一为 mA。
- O20 的当前速度统一为 RPM；目标扭矩保留 `0–1000` 整数范围并明确每单位 `6.5 mA`。
- L20Lite、L20 的速度和扭矩继续明确为无量纲值。
- 增加物理单位、量化边界和兼容入口的自动化回归测试。

## 0.1.0 - 2026-08-17

- 独立提供 `linkerbot_hand` 统一设备接口和 `linkerbot.hand` 兼容入口。
- 包含 L6、O6、L20Lite、L20、L30、O20 和 O30 驱动与能力描述。
- 使用独立 `linkerbot-transport` 包提供 CAN/CAN FD、错误和并发原语。
- 保留统一百分比、协议原始值、非阻塞调度、缓存快照、健康状态和安全恢复契约。
- 角度映射配置遵循 `XDG_CONFIG_HOME`，未设置时继续使用 `~/.config`。
- 默认 README 提供完整中文说明，并保留独立英文版本。
