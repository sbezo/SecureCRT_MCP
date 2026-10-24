# SecureCRT MCP

A FastMCP server that lets Codex work through an **existing authenticated SecureCRT tab** on macOS. SecureCRT keeps the connection, credentials, login state, jump-host shell, and CLI context. This project does not open another SSH connection or read/copy saved credentials.

```text
Codex ── stdio/MCP ── securecrt_server.py ── bridge_client.py
                                             │ private Unix socket (JSON)
SecureCRT tab ◀── Screen.Send / ReadString ── bridge_core.py
```

You start `securecrt_bridge.py` inside SecureCRT for each tab to share. It loads `bridge_core.py` and `profile_controller.py`; the selected policy is in `command_profiles.py`. The bridge binds to that fixed tab, not whichever tab is later selected in the GUI.

The `Screen` object is SecureCRT API terminology. The bridge captures new terminal text while a job is active. It does not read scrollback or call `Screen.Get`, take screenshots, use OCR, activate tabs, or control the desktop.

> **Status:** Experimental. Validate against a lab or disposable session before using it with production devices. This project has not had an independent security audit or broad compatibility/performance testing, and no automated MCP regression tests are currently included.

## Requirements

- macOS and SecureCRT with a compatible Python 3 scripting engine.
- Python 3.10 or later for the MCP server.
- Codex CLI with MCP support.

The SecureCRT-embedded Python engine is separate from the Python environment used to run the MCP server. If SecureCRT reports a Python-engine error, follow its version guidance and VanDyke's [macOS Python setup instructions](https://www.vandyke.com/support/tips/how-to-use-python-scripting-securecrt-on-macos.html).

## Install and connect Codex

Run these commands from the checked-out MCP directory (the directory containing this README):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
codex mcp add securecrt -- "$(pwd)/.venv/bin/python" "$(pwd)/securecrt_server.py"
```

This registers a stdio server. Codex starts it; there is no separate server command, TCP listener, or manual MCP-server launch. Restart/reload Codex if the current session has not loaded the registration. The SecureCRT bridge still needs to be started manually. You may need to update the registered paths after moving the checkout.

## Start a SecureCRT bridge

For each tab you intend to share:

1. Connect normally, finish login/autologin, and leave an **empty, idle command prompt**.
2. In SecureCRT choose **Script → Run** and select `securecrt_bridge.py` (or use a button configured for that script).
3. Choose the profile matching the tab in the prompt. The current order is `1` PANOS-readonly, `2` PANOS-full, `3` IOSXE-readonly, `4` IOSXE-full, `5` Linux-full. Cancel or leave the answer empty to abort.
4. Leave the bridge script running. Repeat in each additional tab; each bridge gets a separate session ID and profile.

The profile declares the expected shell (`panos`, `iosxe`, or `linux`) and permissions; it does **not** detect or verify the connected device. Choose the matching profile yourself. The agent cannot change it. No Accessibility or Screen Recording permission is required. A login script must finish before starting the bridge.

## Tools and first command

Profiles are defined in [command_profiles.py](command_profiles.py). The two `*-readonly` profiles allow commands beginning with `show` or `ping`, using simple arguments. Separators and other special characters are rejected. Interactive input in those profiles is limited to one space for pager advance, without Enter. Full profiles permit any printable single-line command and printable interactive input. Restart the MCP server and each bridge after editing profiles.

`list_sessions` reports `allowed_command_prefixes` from the loaded profile. A null value means unrestricted commands only when a profile is selected.

| Tool | Purpose |
| --- | --- |
| `list_sessions` | Shared tab IDs, captions, saved session names, active profile, device shell, prompt, and recent jobs |
| `probe_prompt` | Send Enter at an empty idle prompt and learn the prompt from incoming text |
| `run_command` | Send one command and return captured output and a job ID |
| `read_output` | Read more output using the returned `next_cursor` |
| `send_input` | Answer an interactive question or send a space to a pager |
| `interrupt_command` | Send Ctrl+C to a specific active job |
| `detach_session` | Stop sharing without closing the SecureCRT connection |

The selected profile declares `device_shell`: `IOSXE-*` → `iosxe` (Cisco IOS XE CLI), `PANOS-*` → `panos` (PAN-OS CLI), and `Linux-full` → `linux` (Linux shell). `device_shell_source` is `user_selected_profile`; this is not automatic detection. If the tab changes device or shell, stop and restart the bridge with the matching profile.

First call `list_sessions`, select the intended `session_id`, then call `probe_prompt(session_id)` and an allowed diagnostic command (for example, `show clock` in `IOSXE-readonly`). **Prompt probing sends Enter.** Only probe when the tab is at an empty, idle prompt—not while a command is partially typed, a program is running, or login is incomplete.

`run_command` accepts `expected_prompt` to override discovery. Supply the full literal prompt (for example, `router#`), not a regular expression or just `#`. When changing CLI mode, supply the prompt expected in the new mode. Output includes command echo and the matched prompt. A prompt match indicates the CLI returned; it does not prove command success. This interactive connection has no portable exit status or separate stdout/stderr.

`wait_seconds` bounds this MCP call's wait (default 10, maximum 30). `timeout_seconds` marks an unfinished job `timed_out` (default 30, maximum 300). **Neither timeout nor cancelling an MCP request stops the remote command.** Capture continues and the tab remains busy. Read the same job or explicitly interrupt it; do not resend it. If a transport fails after sending, inspect `recent_job_ids` before retrying.

Pager handling is explicit: read output, then call `send_input(..., text=" ")` for the next page. Nothing automatically answers confirmations. Avoid manual typing while a job is active. Use **Script → Cancel** in SecureCRT or `detach_session` to stop the bridge; neither automatically interrupts a remote command. The original capture settings are restored during normal cleanup.

## Security and trust model

- The bridge can act with the permissions of the connected SecureCRT session. Treat enabling a tab as granting the MCP client the ability to issue commands and send interactive input within the selected profile. Use only tabs and accounts you are authorized to expose.
- `*-full` profiles are unrestricted for printable single-line commands and interactive input. They can make configuration changes or other consequential actions. Use them only when that level of access is intended; prefer a least-privilege device account and a lab session.
- The profiles named `*-readonly` are **prefix restrictions, not a security sandbox or guarantee of harmless commands**. They allow broad `show` and `ping` command families, which can reveal sensitive information or affect network resources. Tighten the prefixes for your environment.
- The Unix socket is local, in `/tmp/securecrt-mcp-<uid>/`, with a 0700 directory and 0600 socket. This limits access to the same macOS user; it does not authenticate individual processes running as that user. Do not run untrusted local programs as that account while the bridge is active.
- `list_sessions` returns the SecureCRT tab caption and saved-session path as metadata. Command text and captured output are returned to the MCP client and may be included in Codex/model context. Do not share tabs that display secrets or sensitive data unless permitted by your organization's policy.
- Terminal output is untrusted data. It may contain misleading instructions or sensitive information; the agent should treat it as data, not authority.
- No credentials are deliberately read, extracted, or copied by this project. This is not a security audit or guarantee about SecureCRT, the MCP runtime, Codex, or the remote device.

## Behavior and limitations

- MCP uses stdio with Codex; MCP-to-bridge communication uses a local Unix socket, not TCP. No separate SSH connection is opened.
- One bridge shares one fixed tab and allows one active command job at a time. Each additional tab needs its own bridge and profile. Concurrent-script behavior depends on the installed SecureCRT build and should be tested. On observed disconnect the bridge exits; restart it after reconnecting.
- Only new data during an active job is captured. Idle output, old screen contents, and scrollback are not retained. Capture is for text CLIs, not full-screen applications. `IgnoreEscape` removes terminal escape sequences; this is not a byte-exact recorder. A Unicode-only fragment at a `ReadString` timeout may be lost.
- Completion is inferred when the full expected prompt matches the current line after one second of stream inactivity. Similar text in ordinary output can produce a false completion. CLI mode changes and unusual prompts may need `expected_prompt`.
- The bridge keeps at most 1,000,000 characters per job and the ten most recent jobs in memory. Responses return 32,000 characters by default; use cursors to read further. Truncation is reported. No transcript file is written.
- A crashed bridge can leave a stale socket; `list_sessions` reports unavailable bridges. Session IDs are random UUIDs, so a restarted bridge gets a new ID.

## Code map

- [Toy_examples](Toy_examples/README.md) — a standalone beginner example with line-by-line comments and `send`, `read`, and `stop` commands. It uses localhost TCP and reads the visible screen; the MCP bridge behavior and protections described above do not apply to it.
- [securecrt_server.py](securecrt_server.py) — FastMCP tools exposed to Codex; stdio transport.
- [bridge_client.py](bridge_client.py) — async local RPC client; does not automatically retry writes whose delivery is uncertain.
- [securecrt_bridge.py](securecrt_bridge.py) — SecureCRT script entry point; loads the bridge and profile selector.
- [bridge_core.py](bridge_core.py) — private Unix socket server, request queue, SecureCRT adapter, job state, and output capture. Only the SecureCRT script thread calls `crt`.
- [profile_controller.py](profile_controller.py) — SecureCRT prompt that asks which profile to use for the tab.
- [command_profiles.py](command_profiles.py) — profile names, declared shells, and command-prefix permissions.
- [requirements.txt](requirements.txt) — MCP server dependency constraints.

To follow one command, start with `run_command()` in `securecrt_server.py`, follow `request()` in `bridge_client.py`, then inspect `SessionEngine._start()` and `SessionEngine.pump()` in `bridge_core.py`.

Vendor references: [SecureCRT process boundary](https://www.vandyke.com/support/scripting/scripting-examples/python-importing-custom-modules-referencing-the-crt-object.html), [ReadString output capture](https://www.vandyke.com/support/tips/readstring.html), and [SecureCRT scripting synchronization](https://www.vandyke.com/support/securecrt/scripting_faq.html).
