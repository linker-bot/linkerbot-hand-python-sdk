# 发布流程

面向维护者。使用者的安装说明在仓库根 `README.md`。

## 为什么有这个脚本

发布这个包意味着**六件事**保持一致：`pyproject.toml` 的版本号、`CHANGELOG.md` 的带日期章节、两个 README 里钉住的版本、git 签名标签、registry 里的产物、以及带附件的 Gitea Release。2.0.0 那次前两件做对了、中间三件全漏了，结果是「版本发了但没人装得上」：文档仍指向 1.0.0、没有 `v2.0.0` 标签、registry 里只有 1.0.0，而 ROS 声明的 `linkerbot-hand>=2.0.0` 无法满足。补救过程本身也出过错——在文档修正 PR 还没合并时打了标签，标签指向了文档错误的提交，只能删掉远端标签重做。补齐之后**第六件仍然漏着**：`v2.0.0` 一度是本仓 9 个标签中唯一没有 Release 的一个，因为没有任何门禁让它显形。

这些不是「更仔细一点」能解决的，所以做成机械校验。

## 分工

**打标签是人的动作，上传是自动化的动作。** `release.py tag` 推送签名标签，标签推送触发 `.gitea/workflows/release.yml` 执行 `release.py publish`。两者跑的是同一个脚本，因此只有一套门禁，不存在本地一套、CI 另一套。

runner 当前离线，工作流推送后只会排队不执行，所以现阶段两步都在本地跑。runner 上线后只需要跑 `tag`。

`publish` 是幂等的：registry 里已有该版本时跳过上传，Release 已存在时跳过创建，因此本地跑过之后 CI 再跑一遍不会重复发布。

## publish 必须从标签构建

`publish` 要求 `v<版本>` 存在**且正好指向 HEAD**。这是 `tag` 那道门禁的反面，是刻意的：标签的全部意义就是记录 registry 里那个产物由哪份源码构建，从别的提交构建就把它唯一的作用毁掉了。

所以本地发布的顺序是：

```bash
python scripts/release.py tag            # 在 main 上
git checkout v<版本>
python scripts/release.py publish
```

CI 里工作流直接 checkout 标签，这道门禁天然满足。

早先的版本不是这样：`publish` 为适配 CI 的 detached checkout，把工作区与标签两道门禁**整个跳过**了，结果本地跑 `publish` 时没有任何门禁把构建的源码与标签关联起来。实测在 `HEAD` 领先标签 1 个提交、且有未提交改动的情况下四道门禁全绿放行，会上传一份在 git 任何提交中都不存在的源码。registry 的版本不能覆盖，所以不可挽回。

## 三个子命令

```bash
python scripts/release.py check      # 只读，不改动任何东西
python scripts/release.py tag        # check 通过后打签名标签并推送
python scripts/release.py publish    # 构建、校验产物、上传
```

凭据从环境变量读，不写进仓库：

```bash
export GITEA_USERNAME=<用户名>
export GITEA_TOKEN=<只需 write:package 权限的 token>
```

CI 里从 Actions secret 取同名的两个值。**不要复用全权限 token。**

## 门禁清单

`check` 会逐条校验，任一条失败即停且不产生副作用：

| 门禁 | 拦的是什么 |
| --- | --- |
| 工作区 | 已跟踪文件有改动。未跟踪文件只提示不拦——它们是否会进产物由 sdist 门禁负责 |
| 分支 | 不在 `main`，或与 `origin/main` 不一致（`publish` 不校验这一项，它从标签的 detached checkout 运行） |
| CHANGELOG | 缺少带日期的 `## <版本> - YYYY-MM-DD` 章节；或 `## 未发布` 章节仍有内容（说明还没整理成版本） |
| README | 两个 README 未钉住当前版本，或还钉着旧版本 |
| 标签 | `tag` 时：`v<版本>` 已存在。`publish` 时反过来：标签不存在，或不指向 HEAD |
| 质量 | `ruff check` / `ruff format --check` / `pytest` 未通过 |
| registry | 该版本已发布。网络不可达时明确提示「未校验」而非当作「没发布过」——否则一次网络故障就会放过一次重复上传 |

`publish` 额外校验产物本身，而不只是「构建成功」：

| 门禁 | 拦的是什么 |
| --- | --- |
| `twine check` | 产物元数据不合规 |
| wheel 版本 | wheel 元数据里的版本与 `pyproject.toml` 不符 |
| wheel 长描述 | `pyproject.toml` 声明 `readme = "README.md"`，README 会打进 wheel 元数据。若 README 钉着旧版本，那份说明会**永久留在已发布的产物里** |
| sdist 内容 | sdist 里出现 git 未跟踪的文件。工作副本里的临时目录肉眼很难发现，而包一旦上传就不该覆盖或回收 |

上传完成后 `publish` 创建 Gitea Release：标题 `linkerbot-hand <版本>`、正文取自 CHANGELOG 的对应章节、附 wheel 与 sdist（本仓历史 Release 均带这两个附件）。

## 发布范围

只有 **hand** 与 **transport** 需要上传到 registry —— 文档的安装路径从 registry 装这两个。

**`linkerbot-hand-ros` 不上传。** 全仓文档零处让使用者 `pip install linkerbot-hand-ros`；ROS 侧的正式路径是 `git clone --branch v<版本>` 加 `colcon build`。而且即便 pip 装了也用不了：它靠 `data_files` 注册 ament 资源索引与 launch 文件，而 `linkerbot_hand_msgs` 是 ROSIDL 接口、没有平台无关 wheel，装进 venv 后缺消息类型，节点起不来。ROS 仓库的发布只需要打标签。

## 一次发布的完整顺序

1. 改 `pyproject.toml` 的版本号。
2. `CHANGELOG.md` 的 `未发布` 内容移入带日期的版本章节，重建空的 `未发布`。破坏性改动在章节开头列摘要表。
3. 两个 README 的安装示例更新到新版本。
4. 上述三步走正常的 Issue + PR 流程并合并。
5. `python scripts/release.py check` —— 确认全绿。
6. `python scripts/release.py tag`
7. `git checkout v<版本>`，然后 `python scripts/release.py publish`（runner 上线后由 CI 完成）

第 4 步必须在 6、7 之前：标签会指向当前提交，而 README 会打进 wheel。顺序颠倒的话，标签指向文档错误的提交，上传的包永久带着旧版本的安装说明。
