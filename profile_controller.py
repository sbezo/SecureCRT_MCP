"""Ask the SecureCRT user which command profile to apply to this tab."""


def select_profile(crt, profiles):
    lines = []
    for number, profile in enumerate(profiles, start=1):
        lines.append("  %s. %s" % (number, profile))
    options = "\n".join(lines)
    prompt = (
        "Enter the profile number for this SecureCRT tab:\n\n"
        "%s\n\n"
        "The profile also tells the agent this tab's device shell:\n"
        "IOSXE = Cisco IOS XE CLI; PANOS = PAN-OS CLI; Linux = Linux shell.\n\n"
        "Enter a number (1-%s), or leave the field empty to cancel."
        % (options, len(profiles))
    )
    while True:
        selected = crt.Dialog.Prompt(prompt, "SecureCRT Command Profile", "", False)
        if not selected or not selected.strip():
            return None
        selected = selected.strip()
        if selected.isdecimal() and 1 <= int(selected) <= len(profiles):
            return profiles[int(selected) - 1]
        crt.Dialog.MessageBox("Invalid profile number. Choose one of:\n\n%s" % options)
