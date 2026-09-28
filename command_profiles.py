"""Per-tab shell declarations and command permissions. Restart MCP and bridges after edits.

Each profile has a shell and a list of allowed command prefixes.
None means full access. Keep the order stable: the selector numbers follow it.
"""

COMMAND_PROFILES = {
    "PANOS-readonly": {
        "shell": "panos",
        "commands": ["show", "ping"],
    },
    "PANOS-full": {
        "shell": "panos",
        "commands": None,
    },
    "IOSXE-readonly": {
        "shell": "iosxe",
        "commands": ["show", "ping"],
    },
    "IOSXE-full": {
        "shell": "iosxe",
        "commands": None,
    },
    "Linux-full": {
        "shell": "linux",
        "commands": None,
    },
}


def available_command_profiles():
    return list(COMMAND_PROFILES)


def device_shell_for_profile(profile):
    """Return the user's shell declaration, not an automatically detected OS."""
    if profile not in COMMAND_PROFILES:
        return None
    return COMMAND_PROFILES[profile]["shell"]
