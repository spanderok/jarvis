# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pynput", "pyobjc-framework-AVFoundation"]
# ///
"""Do the two macOS permissions Jarvis needs actually reach this app?

Both are granted to an application, not to a script, and both fail silently:
without the microphone he hears nothing but nobody says so, and without Input
Monitoring every key press is dropped before it reaches him. Neither raises,
neither logs - the first one just stays quiet and the second one just does
nothing, which is why the installer asks here instead of leaving it to be
discovered a week later.

  uv run perm_check.py          one line per permission, exit 1 if any is missing
  uv run perm_check.py --quiet  only the ones that are missing
"""
import sys

import hostapp

APP = hostapp.name()

# macOS opens a named pane of System Settings from a URL, and nobody finds
# these lists by scrolling - Privacy & Security is thirty entries long.
MIC_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
KEYS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"


def microphone() -> tuple[str, str]:
    """(state, what to do) - asked of macOS, without opening the microphone."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    except ImportError as e:
        return "unknown", f"could not ask macOS ({e})"
    # 0 not determined, 1 restricted, 2 denied, 3 authorized
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if status == 3:
        return "granted", ""
    if status == 0:
        return "not asked yet", (
            f"macOS will ask {APP} the first time he opens the microphone. "
            f"If no dialog appears, add {APP} by hand under Privacy & Security "
            f"-> Microphone.")
    return "MISSING", (f"System Settings -> Privacy & Security -> Microphone -> "
                       f"add {APP}, then quit it completely and open it again. "
                       f"The pane opens straight from here: "
                       f"open '{MIC_PANE}'")


def input_monitoring() -> tuple[str, str]:
    """Same question for the keys, asked of the Input Monitoring toggle."""
    state = hostapp.can_watch_keys()
    if state == "granted":
        return "granted", ""
    if state == "unknown":
        return "unknown", (f"macOS did not say whether {APP} may watch keys. "
                           f"Press the key and read listener.log")
    return "MISSING", (f"System Settings -> Privacy & Security -> Input Monitoring "
                       f"-> add {APP}, then quit it completely and open it again. "
                       f"The pane opens straight from here: "
                       f"open '{KEYS_PANE}'. "
                       f"Optional: it only buys you the key, and jarvis-key.sh "
                       f"from a Shortcut needs no permission at all.")


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    print(f"permissions for {APP}:")
    bad = 0
    for label, (state, fix) in (("microphone      ", microphone()),
                                ("input monitoring", input_monitoring())):
        if state == "MISSING":
            bad += 1
        if state == "granted" and quiet:
            continue
        print(f"  {label}  {state}")
        if fix:
            for line in fix.split(". "):
                if line.strip():
                    print(f"      {line.strip().rstrip('.')}.")
    raise SystemExit(1 if bad else 0)
