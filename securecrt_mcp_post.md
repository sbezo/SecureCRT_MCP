# Let Codex use my SecureCRT tab

I already wrote about [troubleshooting a network lab with Codex](https://sbezo.github.io/posts/how_to_troubleshoot_mpls_lab_with_codex/). This time I wanted to connect the agent to something I use every day: SecureCRT.

The idea is simple. I am already connected to a device. Login is done, maybe I went through a jump host, and the CLI is ready. Can I give the agent access to this exact session?

So I built a small Python MCP server for it:)

## How it works

There are two parts: an MCP server running on my Mac and a Python bridge running inside SecureCRT.

```text
Codex → MCP server → local Unix socket → SecureCRT tab → device
```

The bridge uses SecureCRT's scripting API to send commands and capture incoming text. The existing connection stays in SecureCRT. There is no additional SSH login or password file for the agent.

I choose which tab to share by starting the bridge there. Switching to another tab does not move the agent to another device.

## Profiles

When the bridge starts, it asks me to select a profile:

- `IOSXE-readonly` or `IOSXE-full`
- `PANOS-readonly` or `PANOS-full`
- `Linux-full`

This tells the agent which device shell it is working with and which commands are allowed.

The current read-only profiles allow `show` and `ping` with simple arguments. Full profiles allow configuration commands too. These rules are defined in `command_profiles.py` and checked by both the server and the bridge.

## Using it

My workflow is short:

1. Connect to the device in SecureCRT and leave an empty CLI prompt.
2. Run `securecrt_bridge.py` through **Script → Run** and select a profile.
3. Ask Codex to list the shared sessions and run a command.

For example:

```text
Use the shared IOS XE tab, run show ip interface brief,
and summarize the interface status.
```

During testing, I also asked Codex to run `write memory` with the full profile. The switch returned `[OK]` and its prompt. A small test, but a useful one.

This is still an experiment on macOS. I am investigating a SecureCRT crash whose report points to macOS accessibility handling; the trigger is not confirmed yet.

But the workflow is what I wanted: open my usual terminal session, share it with the agent, and work from there;)
