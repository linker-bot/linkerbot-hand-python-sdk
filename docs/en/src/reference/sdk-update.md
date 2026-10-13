# Updating the SDK

If you have already installed or used the SDK and need to update the local source, run the following commands from the source root to update the current branch of both repositories:

```bash
# Update the transport source
git -C linkerbot-transport pull --ff-only
# Update the hand SDK source
git -C linkerbot-hand pull --ff-only
```

If Git reports local changes or diverging branches, resolve those issues before continuing.

After updating the source, run the appropriate commands from the current directory to update the SDK in your environment.

## Linux

If originally installed with uv:

```bash
# Activate the SDK virtual environment
source py-linkerbot/bin/activate
# Install both source packages and adapter dependencies
uv pip install -e "./linkerbot-transport[all]"
uv pip install -e ./linkerbot-hand
```

If originally installed with pip:

```bash
# Activate the SDK virtual environment
source py-linkerbot/bin/activate
# Install both source packages and adapter dependencies
python -m pip install -e "./linkerbot-transport[all]"
python -m pip install -e ./linkerbot-hand
```

## Windows PowerShell

```powershell
# Activate the SDK virtual environment
.\py-linkerbot\Scripts\Activate.ps1
# Install both source packages and adapter dependencies
python -m pip install -e "./linkerbot-transport[all]"
python -m pip install -e ./linkerbot-hand
```

After updating the SDK, restart any Python programs that use it.
