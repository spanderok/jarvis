# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pynput"]
# ///
"""Print whatever key you press, in the exact form the Jarvis daemon expects.

Run it in YOUR terminal (not through Claude), press M5, and read the line it
prints - that string goes into JARVIS_TAP_KEYS.

  uv run ~/.claude/jarvis/probe_key.py

Needs macOS Input Monitoring permission for the terminal you run it from:
  System Settings -> Privacy & Security -> Input Monitoring -> add your terminal.
Press Esc to quit.
"""
import hostapp
from pynput import keyboard

MODS = {
    keyboard.Key.cmd: "<cmd>", keyboard.Key.cmd_l: "<cmd>", keyboard.Key.cmd_r: "<cmd>",
    keyboard.Key.shift: "<shift>", keyboard.Key.shift_l: "<shift>",
    keyboard.Key.shift_r: "<shift>",
    keyboard.Key.alt: "<alt>", keyboard.Key.alt_l: "<alt>", keyboard.Key.alt_r: "<alt>",
    keyboard.Key.ctrl: "<ctrl>", keyboard.Key.ctrl_l: "<ctrl>",
    keyboard.Key.ctrl_r: "<ctrl>",
}
held: list[str] = []

if hostapp.can_watch_keys() == "MISSING":
    app = hostapp.name()
    print(f"{app} has no Input Monitoring permission, so no key press will ever\n"
          f"reach this script. Add it here, then quit {app} completely and open\n"
          f"it again:\n\n"
          f"  open 'x-apple.systempreferences:com.apple.preference.security"
          f"?Privacy_ListenEvent'\n")
    raise SystemExit(1)

print("Press the key you want (M5, F13, whatever). Esc quits.\n")


def spec_for(key) -> str:
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char
        return f"<{key.vk}>"  # raw virtual key code, usable in JARVIS_TAP_KEYS
    return f"<{key.name}>"


def on_press(key):
    if key == keyboard.Key.esc:
        print("\nbye")
        return False
    mod = MODS.get(key)
    if mod:
        if mod not in held:
            held.append(mod)
        return
    combo = "+".join(held + [spec_for(key)])
    print(f"raw: {key!r}")
    print(f"JARVIS_TAP_KEYS spec: {combo}\n")


def on_release(key):
    mod = MODS.get(key)
    if mod and mod in held:
        held.remove(mod)


with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
