# $language = "Python"
# $interface = "1.0"
"""Run with SecureCRT > Script > Run in the connected tab to share."""

import os
import runpy


def main(crt):
    # SecureCRT keeps its interpreter alive; run_path loads fresh code on restart.
    folder = os.path.dirname(crt.ScriptFullName)
    core = runpy.run_path(os.path.join(folder, "bridge_core.py"))
    controller = runpy.run_path(os.path.join(folder, "profile_controller.py"))

    # Give the loaded functions ordinary names so the workflow is easy to follow.
    available_profiles = core["available_command_profiles"]
    select_profile = controller["select_profile"]
    run_bridge = core["run_bridge"]

    profile = select_profile(crt, available_profiles())
    if profile is None:
        crt.Dialog.MessageBox("No profile selected. The SecureCRT bridge was not started.")
        return
    run_bridge(crt, profile)


if "crt" in globals():
    main(globals()["crt"])
elif __name__ == "__main__":
    raise SystemExit("Run this script INSIDE SecureCRT: Script > Run.")
