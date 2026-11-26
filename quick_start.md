# Quick start — macOS

Requires **SecureCRT with Python 3 scripting**, **Python 3.10+**, and an installed, signed-in **Codex CLI**.

## 1. Clone and install

Run in Terminal:

```sh
cd ~/your_dev_folder
git clone https://github.com/sbezo/SecureCRT_MCP.git
cd SecureCRT_MCP
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If you already cloned the repository, use your existing checkout instead.

## 2. Register the MCP server

From the `SecureCRT_MCP` directory:

```sh
codex mcp add securecrt -- "$(pwd)/.venv/bin/python" "$(pwd)/securecrt_server.py"
codex mcp list
```

Check that `securecrt` is listed. Codex starts the MCP server automatically; do not run `securecrt_server.py` manually. Restart an already-running Codex session after registration.

## 3. Open SecureCRT

```sh
open -a SecureCRT
```

Connect to your device normally. Finish login and leave the tab at an **empty, idle command prompt**.

## 4. Start the bridge in that tab

In SecureCRT, choose **Script → Run** and select:

```text
~/your_dev_folder/SecureCRT_MCP/securecrt_bridge.py
```

In the file picker, press **Cmd+Shift+G** to enter the path. Adjust it if you cloned elsewhere.

Choose the matching profile, for example **IOSXE-readonly** for Cisco IOS XE. Leave the script running. Repeat for each tab you want to share.

## 5. Test from Codex

Start Codex from Terminal:

```sh
codex
```

For an IOS XE tab, ask:

> Use the SecureCRT MCP server to list the shared sessions. Select the IOS XE tab, probe its empty idle prompt, run `show ip interface brief`, and summarize the interface status.

To test automatic paging:

> Run `show ip interface` in the same tab with automatic paging enabled. Wait for completion and report elapsed time, pages advanced, and any capture warnings.

## 6. Stop or restart

- Stop sharing: **Script → Cancel** in SecureCRT. This does not disconnect the device or stop a running remote command.
- After updating the bridge: wait until the tab is idle, cancel the script, and run `securecrt_bridge.py` again.
- After updating the MCP server: restart Codex too.

Do not type manually in the shared tab while a command job is active. If `capture_may_be_incomplete=true`, do not assume the captured output is complete.

If SecureCRT cannot load Python, see [VanDyke's macOS Python setup guide](https://www.vandyke.com/support/tips/how-to-use-python-scripting-securecrt-on-macos.html). More details: [README](README.md).
