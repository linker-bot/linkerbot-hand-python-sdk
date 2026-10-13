# 更新 SDK

如果你已经安装或使用过 SDK，需要更新本地源码，请在源码根目录中执行以下命令，更新两个仓库的当前分支：

```bash
# 更新传输层源码
git -C linkerbot-transport pull --ff-only
# 更新设备 SDK 源码
git -C linkerbot-hand pull --ff-only
```

如果 Git 提示本地修改或分支分歧，请先处理相关问题，再继续更新。

源码更新完成后，请在当前目录执行对应命令，更新环境中的 SDK。

## Linux

原先使用 uv 安装：

```bash
# 激活 SDK 所在虚拟环境
source py-linkerbot/bin/activate
# 安装两个源码包及适配器依赖
uv pip install -e "./linkerbot-transport[all]"
uv pip install -e ./linkerbot-hand
```

原先使用 pip 安装：

```bash
# 激活 SDK 所在虚拟环境
source py-linkerbot/bin/activate
# 安装两个源码包及适配器依赖
python -m pip install -e "./linkerbot-transport[all]"
python -m pip install -e ./linkerbot-hand
```

## Windows PowerShell

```powershell
# 激活 SDK 所在虚拟环境
.\py-linkerbot\Scripts\Activate.ps1
# 安装两个源码包及适配器依赖
python -m pip install -e "./linkerbot-transport[all]"
python -m pip install -e ./linkerbot-hand
```

更新 SDK 后，请重新运行使用 SDK 的 Python 程序。
