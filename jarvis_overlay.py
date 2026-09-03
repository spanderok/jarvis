# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pyobjc-framework-Cocoa", "pyobjc-framework-Quartz",
#                 "tomli; python_version < '3.11'"]
# ///
"""Floating status capsule for Jarvis - sits above every window, on every space.

Implements the design from the owner's agent-overlay.html mockup (21.08), natively:
a glass capsule with two spinning ring arcs, a breathing pip, a state word and a
small equalizer. Native Core Animation instead of a WKWebView on purpose - the
same mockup rendered in a web view measured 201 MB across four processes with
15.7% of a core burned around the clock (the mockup animates even while idle).
Here every idle-state animation runs on the window server's GPU compositor: the
python process wakes only for the 0.25 s state poll, and the equalizer timer
exists only while the state is active.

Reads the state file the daemon writes. Hidden entirely while no daemon runs.

Run it from your own terminal (it needs a GUI session):
  uv run ~/.claude/jarvis/jarvis_overlay.py

Env:
  JARVIS_OVERLAY_CORNER  tr (default) | tl | br | bl - which screen corner
  JARVIS_OVERLAY_IDLE    0 to hide the capsule while idle (default 1 - always on)
  JARVIS_OVERLAY_MARGIN  gap from the screen edge, default 104
  JARVIS_OVERLAY_DRAG    1 - draggable (costs click-through), 0 - clicks pass through
"""
import math
import os
import pathlib
import random
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lang as lang_mod  # noqa: E402

import objc

warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSColor,
                    NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
                    NSKernAttributeName, NSPanel, NSScreen,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary,
                    NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskNonactivatingPanel)
from AppKit import NSEvent
from AppKit import NSApplicationDidChangeScreenParametersNotification, NSNotificationCenter
from Foundation import (NSAttributedString, NSMakeRect, NSMutableAttributedString,
                        NSObject, NSTimer)
from Quartz import (CABasicAnimation, CAGradientLayer, CAKeyframeAnimation, CALayer,
                    CAMediaTimingFunction, CAShapeLayer, CATextLayer, CATransaction,
                    CGPathAddArc, CGPathAddLineToPoint, CGPathAddQuadCurveToPoint,
                    CGPathCreateMutable,
                    CGPathCreateWithRoundedRect, CGPathMoveToPoint,
                    CGRectMake, kCAMediaTimingFunctionEaseInEaseOut,
                    kCAMediaTimingFunctionLinear)

JARVIS_DIR = pathlib.Path(os.path.expanduser("~/.claude/jarvis"))
STATE_FILE = JARVIS_DIR / "state"
# Which Claude session holds the microphone - shown as the caption.
OWNER_FILE = JARVIS_DIR / "listener.owner"
POS_FILE = JARVIS_DIR / "overlay.pos"
# The lock any agent takes while speaking through the voice-answer skill. It is
# the only honest sign that someone is talking: the full Jarvis daemon sets
# "speaking" for its own voice, but it never watches this lock - that branch lives
# in listen_only_main() alone. So on 22.08 the owner heard an agent report while the
# badge sat there asleep. The badge is a display; it should believe the lock.
SPEAK_LOCK = pathlib.Path(os.path.expanduser("~/.claude/tts-cache/.speak.lock"))

CORNER = os.environ.get("JARVIS_OVERLAY_CORNER", "tr")
SHOW_IDLE = os.environ.get("JARVIS_OVERLAY_IDLE", "1") == "1"
# 104: first 24 put the badge tight under the menu bar, then 64 was still too
# high for the owner - they asked for 40 points lower twice (history of 20.08)
MARGIN = int(os.environ.get("JARVIS_OVERLAY_MARGIN", "104"))
DRAGGABLE = os.environ.get("JARVIS_OVERLAY_DRAG") == "1"
# The window is 400x108 while the capsule inside it is 52 tall and rarely wider
# than 290 - the rest is transparent room for the glow. With dragging on, that
# whole rectangle used to swallow clicks meant for whatever is underneath. So the
# window catches the mouse only while the cursor is actually over the capsule:
# the cursor position is checked on a timer and mouse events are switched on and
# off accordingly. Checked often enough that a click is never missed - 0.06 s
# against a human aiming at a 52 pt target.
HIT_POLL = float(os.environ.get("JARVIS_OVERLAY_HIT_POLL", "0.06"))
# ...but never take the mouse away mid-drag: while a button is held the window
# keeps catching events wherever the cursor wanders, or the badge would be dropped
# the moment the pointer left it.
STATUS_WINDOW_LEVEL = 25
# Colour of the session name in the capsule. Sampled off the screenshot the owner
# sent on 21.08 - the same yellow-green his terminal paints strings with, #baba44.
# The state word stays white; only the name is tinted, so the eye finds "whose
# ears are these" without reading the whole line.
CAPTION_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.73, 0.73, 0.27, 1.0)

# ── geometry (points), taken from the mockup ──────────────────────────────────
CAPSULE_H = 52
COLLAPSED_W = 52
CORE = 36                    # ring block
PAD = 8                      # capsule side padding
GAP_LABEL = 12               # core -> label
GAP_VIZ = 12                 # label -> viz
PAD_RIGHT = 6
BAR_COUNT = 11
VIZ_H = 22
# window is a fixed-size transparent sheet; only the capsule layer inside moves.
# Fixed size means the window server never re-shapes the surface mid-animation.
# It has to be taller and wider than the capsule by more than the shadow radius:
# the first version left 12 pt above and below a 52 pt capsule while the glow
# blurred over 32 pt, and the window edge sliced the glow into a rectangle with
# visible straight top and bottom lines - the owner sent the screenshot on 21.08.
# 12 instead of 28: the shadow itself shrank (radius 4 + glow*6, so at most 10 pt),
# and every point of this margin is window that is not capsule. In sleep, where
# the owner mostly sees it, the capsule is a 52 pt circle inside it.
SHADOW_ROOM = 12
WIN_W = 400
WIN_H = CAPSULE_H + 2 * SHADOW_ROOM
FPS = 30                     # equalizer refresh while active; 0 wakeups in idle
# The swarm drifts slowly - at 30 fps a particle moves well under a pixel per
# frame, so a third of the frames were redrawing the same picture. Measured on
# 21.08: the swarm cost 0.42 s of cpu over ten seconds at 30 fps, which is 4.2%
# of a core. The equalizer keeps the full 30: its bars move fast and a lower rate
# shows as steps.
SWARM_FPS = 20

# ── the "thinking" swarm, ported from the owner's thinking-swarm.html (21.08) ─────
# Particles drift along their own slow Lissajous loops; whenever two come closer
# than SWARM_LINK they are joined by a bowed line, and a particle with many links
# burns brighter. Numbers are the mockup's own - not picked by me.
SWARM_DOTS = 11
SWARM_LINK = 26.0          # below this distance a link appears
SWARM_LINK_W = 1.15        # link stroke width at full strength
SWARM_FADE = 1.15          # higher = links vanish faster with distance
SWARM_DOT_R = 1.7
SWARM_BOW = 0.18           # how much a link curves; 0 would draw a straight rule
SWARM_MAX_LINKS = 30       # layer pool ceiling, same as the mockup
SWARM_W = 84               # the mockup's svg is 84x22

# ── the "listening" wave, ported from the owner's listening-wave.html (21.08) ─────
# A scrolling trace of the real microphone signal. The daemon writes signed peaks
# to ~/.claude/jarvis/level; this process only draws them, because the microphone
# belongs to the daemon. Numbers are the mockup's own.
WAVE_SAMPLES = 42          # points in the visible window
WAVE_AMP = 10.4            # vertical reach in slot units, 11 is the edge
WAVE_WIDTH = 1.6           # stroke width of the trace
# 0.22 instead of the mockup's 0.45: at 0.45 each new sample only moved the line
# a bit over half way, so a syllable arrived smeared and the owner asked for a
# sharper reaction. At 0.22 a new sample carries 78% of the step. The mockup
# warns that below 0.3 the line starts to twitch - it does, and that twitch is
# what reads as "reacting".
WAVE_SMOOTH = 0.22         # 0 raw and jittery, 1 sluggish
WAVE_IDLE_WOB = 0.06       # faint life in the line while nobody speaks
WAVE_FPS = 32              # one sample consumed per frame; the daemon writes 32/s
WAVE_W = 84
WAVE_MID = 11.0            # the axis, half of the 22 pt slot
LEVEL_FILE = JARVIS_DIR / "level"
# The envelope of the phrase being spoken, written by whoever plays it: start
# time, values per second, then signed peaks. We look up "where are we now"
# instead of listening to the speakers, which the badge cannot do anyway.
SPEAK_LEVEL_FILE = JARVIS_DIR / "speak_level"

STATES = {
    # state: (label, hue rgb, glow 0..1, ring_a s, ring_b s, pip s, viz width)
    "idle":      ("asleep",  (0.49, 0.56, 0.68), 0.12, 26.0, 19.0, 4.2, 0),
    "listening": ("listening", (0.32, 0.86, 1.00), 1.00, 7.0,  6.0,  4.2, WAVE_W),
    "thinking":  ("thinking", (1.00, 0.71, 0.30), 0.82, 2.4,  1.7,  1.4, SWARM_W),
    "speaking":  ("talking", (0.62, 0.91, 1.00), 0.90, 5.0,  6.0,  4.2, WAVE_W),
}


# The state word is a word of the language he is speaking, like everything else
# he says - locales/<lang>.toml, table [badge]. The English ones stay in STATES
# above as the last resort: a badge that shows the wrong word is better than a
# badge that shows none because a locale would not load.
_badge_cache: dict = {"stamp": None, "words": {}}


def spoken_language() -> str:
    """Which language the daemon is running in, read the way the daemon reads it.

    The last JARVIS_LANG in jarvis.env wins, because that is what `set -a; .` in
    the shell does with a repeated assignment - and the first-wake setup appends
    rather than rewrites.
    """
    lang = os.environ.get("JARVIS_LANG", "").strip()
    if lang:
        return lang
    try:
        for line in (JARVIS_DIR / "jarvis.env").read_text(
                encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("JARVIS_LANG="):
                lang = line.partition("=")[2].strip().strip('"').strip("'")
    except OSError:
        pass
    return lang or lang_mod.DEFAULT_LANG


def badge_word(state: str) -> str:
    """The word for this state, re-read whenever jarvis.env changes.

    It changes exactly once for most people - when the language is chosen out
    loud on the first wake - and the badge should follow without being
    restarted.
    """
    try:
        stamp = (JARVIS_DIR / "jarvis.env").stat().st_mtime
    except OSError:
        stamp = 0.0
    if _badge_cache["stamp"] != stamp:
        try:
            _badge_cache["words"] = dict(lang_mod.load(spoken_language()).badge)
        except (lang_mod.LocaleError, OSError):
            _badge_cache["words"] = {}
        _badge_cache["stamp"] = stamp
    return _badge_cache["words"].get(state) or STATES[state][0]


def daemon_running() -> bool:
    """Checked by pid files, not pgrep - pgrep also matched the checking command
    itself and the /assist-off kill loop, and the badge flashed a stale state."""
    for name in ("daemon.pid", "listener.pid"):
        try:
            pid = int((JARVIS_DIR / name).read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
    return False


def hue_color(rgb, alpha=1.0):
    r, g, b = rgb
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha).CGColor()


def arc_path(radius: float, start_deg: float, sweep_deg: float):
    """One ring arc around the core centre (18, 18)."""
    path = CGPathCreateMutable()
    a0 = math.radians(start_deg)
    a1 = math.radians(start_deg + sweep_deg)
    CGPathAddArc(path, None, 18.0, 18.0, radius, a0, a1, False)
    return path


def label_string(text: str, caption: str):
    """The state word plus the microphone owner, styled like the mockup."""
    word = NSMutableAttributedString.alloc().initWithAttributedString_(
        NSAttributedString.alloc().initWithString_attributes_(
            text.upper(), {
                NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, 0.23),
                NSForegroundColorAttributeName:
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.92),
                NSKernAttributeName: 1.6,
            }))
    if caption:
        word.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                " · ", {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, 0.0),
                    NSForegroundColorAttributeName:
                        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.28),
                }))
        word.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                caption, {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, 0.2),
                    NSForegroundColorAttributeName: CAPTION_COLOR,
                    NSKernAttributeName: 0.9,
                }))
    return word


class Capsule(NSObject):
    def init(self):
        self = objc.super(Capsule, self).init()
        screen = NSScreen.mainScreen()
        self.scale = screen.backingScaleFactor() if screen else 2.0

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self.window_frame(screen),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            2, False)
        self.panel.setLevel_(STATUS_WINDOW_LEVEL)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)          # the glow layer is the shadow
        self.panel.setIgnoresMouseEvents_(True)   # turned off when the cursor is on the capsule
        self.panel.setMovableByWindowBackground_(DRAGGABLE)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)

        root = self.panel.contentView()
        root.setWantsLayer_(True)
        self.build_layers(root.layer())

        self.state = None
        self.owner = ""
        self.shown = False
        self.bars_timer = None
        self.bars_interval = 0.0
        self.tick = 0
        self.levels = [0.08] * BAR_COUNT
        self.targets = [0.08] * BAR_COUNT
        self.speak_started = 0.0
        self.swarm_t0 = time.monotonic()
        self.wave_buf = [0.0] * WAVE_SAMPLES
        self.wave_prev = 0.0
        self.wave_level = 0.0
        self.wave_seq = -1       # which level line we have already read
        self.wave_queue = []     # samples not shown yet
        self.speak_env = []      # the envelope of the phrase being spoken
        self.speak_start = 0.0
        self.speak_rate = 32.0
        self.speak_mtime = None
        self.catching = False    # are we catching the mouse right now
        self.cap_w = COLLAPSED_W  # the capsule's current width, for cursor hit tests
        self.spot = None
        self.right_anchored = CORNER.endswith("r")
        if DRAGGABLE:
            try:
                x, y = POS_FILE.read_text().split()
                self.spot = (float(x), float(y))
            except (OSError, ValueError):
                pass
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "screensChanged:", NSApplicationDidChangeScreenParametersNotification, None)
        return self

    # ── layer tree ─────────────────────────────────────────────────────────────

    @objc.python_method
    def build_layers(self, root):
        # capsule is anchored to its right edge: a width change grows leftwards,
        # so in the top-right corner it never walks off the screen
        cap_y = (WIN_H - CAPSULE_H) / 2.0
        right = WIN_W - SHADOW_ROOM

        # the glow: an empty layer whose explicit shadowPath does the shining.
        # A layer that clips its content (the capsule) cannot draw a shadow, so
        # the shadow lives on this second, unclipped layer behind it.
        self.glow = CALayer.layer()
        self.glow.setFrame_(CGRectMake(right - COLLAPSED_W, cap_y, COLLAPSED_W, CAPSULE_H))
        self.glow.setShadowOffset_((0, 0))
        root.addSublayer_(self.glow)

        self.capsule = CALayer.layer()
        self.capsule.setAnchorPoint_((1.0, 0.5))
        self.capsule.setBounds_(CGRectMake(0, 0, COLLAPSED_W, CAPSULE_H))
        self.capsule.setPosition_((right, cap_y + CAPSULE_H / 2.0))
        self.capsule.setCornerRadius_(CAPSULE_H / 2.0)
        self.capsule.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.07, 0.086, 0.118, 0.62).CGColor())
        self.capsule.setBorderWidth_(1.0)
        self.capsule.setMasksToBounds_(True)
        root.addSublayer_(self.capsule)

        # core: static circle, two ring groups, pip
        self.core = CALayer.layer()
        self.core.setFrame_(CGRectMake(PAD, (CAPSULE_H - CORE) / 2.0, CORE, CORE))
        self.capsule.addSublayer_(self.core)

        def shape(path, width, opacity):
            s = CAShapeLayer.layer()
            s.setPath_(path)
            s.setFillColor_(None)
            s.setLineWidth_(width)
            s.setLineCap_("round")
            s.setOpacity_(opacity)
            s.setFrame_(CGRectMake(0, 0, CORE, CORE))
            s.setContentsScale_(self.scale)
            return s

        self.static_ring = shape(arc_path(15.5, 0, 360), 1.0, 0.16)
        self.core.addSublayer_(self.static_ring)

        self.ring_a = CALayer.layer()
        self.ring_a.setFrame_(CGRectMake(0, 0, CORE, CORE))
        self.ring_a_arcs = [shape(arc_path(14.5, 90, -62), 1.6, 0.9),
                            shape(arc_path(14.5, 270, -62), 1.6, 0.55)]
        for a in self.ring_a_arcs:
            self.ring_a.addSublayer_(a)
        self.core.addSublayer_(self.ring_a)

        self.ring_b = CALayer.layer()
        self.ring_b.setFrame_(CGRectMake(0, 0, CORE, CORE))
        self.ring_b_arcs = [shape(arc_path(10.0, 90, 55), 1.3, 0.7),
                            shape(arc_path(10.0, 270, 55), 1.3, 0.4)]
        for a in self.ring_b_arcs:
            self.ring_b.addSublayer_(a)
        self.core.addSublayer_(self.ring_b)

        self.pip = CAShapeLayer.layer()
        dot = CGPathCreateMutable()
        CGPathAddArc(dot, None, 18.0, 18.0, 4.2, 0, math.tau, False)
        self.pip.setPath_(dot)
        self.pip.setFrame_(CGRectMake(0, 0, CORE, CORE))
        self.pip.setContentsScale_(self.scale)
        self.core.addSublayer_(self.pip)

        self.pip_ring = shape(arc_path(7.4, 0, 360), 1.0, 0.3)
        self.core.addSublayer_(self.pip_ring)

        # body: label + viz, faded out while idle
        self.label = CATextLayer.layer()
        self.label.setContentsScale_(self.scale)
        self.label.setAnchorPoint_((0.0, 0.5))
        self.capsule.addSublayer_(self.label)

        self.viz = CALayer.layer()
        self.viz.setMasksToBounds_(True)
        self.capsule.addSublayer_(self.viz)

        self.bars = []
        for _ in range(BAR_COUNT):
            b = CALayer.layer()
            b.setCornerRadius_(2.2)
            self.viz.addSublayer_(b)
            self.bars.append(b)

        # the listening wave: an axis and one trace, in their own layer
        self.wave = CALayer.layer()
        self.wave.setMasksToBounds_(False)
        self.wave.setHidden_(True)
        self.capsule.addSublayer_(self.wave)

        self.wave_axis = CAShapeLayer.layer()
        self.wave_axis.setFillColor_(None)
        self.wave_axis.setLineWidth_(0.7)
        self.wave_axis.setOpacity_(0.12)
        self.wave_axis.setContentsScale_(self.scale)
        self.wave.addSublayer_(self.wave_axis)

        self.wave_trace = CAShapeLayer.layer()
        self.wave_trace.setFillColor_(None)
        self.wave_trace.setLineWidth_(WAVE_WIDTH)
        self.wave_trace.setLineCap_("round")
        self.wave_trace.setLineJoin_("round")
        self.wave_trace.setContentsScale_(self.scale)
        self.wave.addSublayer_(self.wave_trace)

        # the swarm lives in its own layer so switching states is one hidden flag
        # instead of rebuilding anything
        self.swarm = CALayer.layer()
        self.swarm.setMasksToBounds_(False)
        self.swarm.setHidden_(True)
        self.capsule.addSublayer_(self.swarm)

        self.swarm_links = []
        for _ in range(SWARM_MAX_LINKS):
            l = CAShapeLayer.layer()
            l.setFillColor_(None)
            l.setLineCap_("round")
            l.setOpacity_(0.0)
            l.setContentsScale_(self.scale)
            self.swarm.addSublayer_(l)
            self.swarm_links.append(l)

        self.swarm_dots = []
        for _ in range(SWARM_DOTS):
            d = CALayer.layer()
            d.setOpacity_(0.7)
            self.swarm.addSublayer_(d)
            self.swarm_dots.append(d)

        # each particle keeps its own loop: centre, radii, speeds, phases
        self.swarm_seeds = [{
            "ax": 6 + random.random() * 72, "ay": 4 + random.random() * 14,
            "rx": 6 + random.random() * 9, "ry": 3 + random.random() * 5,
            "sx": 0.00035 + random.random() * 0.0005,
            "sy": 0.0005 + random.random() * 0.0007,
            "px": random.random() * 6.28, "py": random.random() * 6.28,
        } for _ in range(SWARM_DOTS)]

        # shimmer sweep across the capsule while thinking
        self.sheen = CAGradientLayer.layer()
        self.sheen.setStartPoint_((0.0, 0.5))
        self.sheen.setEndPoint_((1.0, 0.5))
        self.sheen.setOpacity_(0.0)
        self.capsule.addSublayer_(self.sheen)

    # ── infinite GPU animations ────────────────────────────────────────────────

    @staticmethod
    def spin(layer, duration, reverse=False):
        """Rotation runs on the window server; this process never wakes for it."""
        old = layer.presentationLayer()
        frm = old.valueForKeyPath_("transform.rotation") if old else 0.0
        a = CABasicAnimation.animationWithKeyPath_("transform.rotation")
        a.setFromValue_(frm)
        a.setByValue_(-math.tau if not reverse else math.tau)
        a.setDuration_(duration)
        a.setRepeatCount_(1e9)
        a.setTimingFunction_(CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionLinear))
        layer.removeAnimationForKey_("spin")
        layer.addAnimation_forKey_(a, "spin")

    @staticmethod
    def breathe(layer, duration):
        g = CABasicAnimation.animationWithKeyPath_("transform.scale")
        g.setFromValue_(0.72)
        g.setToValue_(1.0)
        o = CABasicAnimation.animationWithKeyPath_("opacity")
        o.setFromValue_(0.45)
        o.setToValue_(1.0)
        for a in (g, o):
            a.setDuration_(duration)
            a.setAutoreverses_(True)
            a.setRepeatCount_(1e9)
            a.setTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut))
            layer.addAnimation_forKey_(a, "breathe-" + a.keyPath())

    @objc.python_method
    def wake_pop(self):
        k = CAKeyframeAnimation.animationWithKeyPath_("transform.scale")
        k.setValues_([0.86, 1.06, 1.0])
        k.setKeyTimes_([0.0, 0.55, 1.0])
        k.setDuration_(0.5)
        self.capsule.addAnimation_forKey_(k, "wake")

    @objc.python_method
    def sheen_sweep(self, on: bool, width: float):
        self.sheen.removeAnimationForKey_("sweep")
        self.sheen.setOpacity_(1.0 if on else 0.0)
        if not on:
            return
        self.sheen.setFrame_(CGRectMake(0, 0, width, CAPSULE_H))
        a = CABasicAnimation.animationWithKeyPath_("position.x")
        a.setFromValue_(-width / 2.0)
        a.setToValue_(width * 1.5)
        a.setDuration_(1.7)
        a.setRepeatCount_(1e9)
        a.setTimingFunction_(CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionLinear))
        self.sheen.addAnimation_forKey_(a, "sweep")

    # ── window placement (same rules as the old badge) ─────────────────────────

    @objc.python_method
    def window_frame(self, screen):
        vis = screen.visibleFrame()
        x = vis.origin.x + MARGIN if CORNER.endswith("l") else \
            vis.origin.x + vis.size.width - WIN_W - MARGIN
        y = vis.origin.y + MARGIN if CORNER.startswith("b") else \
            vis.origin.y + vis.size.height - WIN_H - MARGIN
        return NSMakeRect(x, y, WIN_W, WIN_H)

    @objc.python_method
    def remember_spot(self):
        if not DRAGGABLE:
            return
        f = self.panel.frame()
        edge = f.origin.x + f.size.width if self.right_anchored else f.origin.x
        spot = (edge, f.origin.y)
        if self.spot and abs(spot[0] - self.spot[0]) < 1 and abs(spot[1] - self.spot[1]) < 1:
            return
        self.spot = spot
        try:
            POS_FILE.write_text(f"{spot[0]:.0f} {spot[1]:.0f}")
        except OSError:
            pass

    @objc.python_method
    def place(self):
        screen = NSScreen.mainScreen()
        rect = self.window_frame(screen)
        if self.spot:
            x = self.spot[0] - WIN_W if self.right_anchored else self.spot[0]
            rect = NSMakeRect(x, self.spot[1], WIN_W, WIN_H)
            # The remembered spot may belong to a display that is no longer
            # there - dragged on the external monitor, restored on the laptop -
            # and then the badge sits outside the visible area and looks gone.
            # Only the capsule has to be on screen, not the whole 400 pt window.
            vis = screen.visibleFrame()
            cap_r = rect.origin.x + WIN_W - SHADOW_ROOM if self.right_anchored \
                else rect.origin.x + SHADOW_ROOM + COLLAPSED_W
            cap_l = cap_r - COLLAPSED_W
            on_screen = (cap_l >= vis.origin.x
                         and cap_r <= vis.origin.x + vis.size.width
                         and rect.origin.y >= vis.origin.y
                         and rect.origin.y + WIN_H <= vis.origin.y + vis.size.height)
            if not on_screen:
                print("capsule: remembered spot is off this screen, back to the corner", flush=True)
                self.spot = None
                try:
                    POS_FILE.unlink()
                except OSError:
                    pass
                rect = self.window_frame(screen)
        self.panel.setFrame_display_(rect, False)

    def screensChanged_(self, note):
        # A monitor was plugged in or pulled out: the frame we hold may now be
        # in a space that no longer exists. Re-place while visible; a hidden
        # badge is placed anyway the next time it is shown.
        if self.shown:
            self.place()

    # ── state machine ──────────────────────────────────────────────────────────

    @objc.python_method
    def read_state(self) -> str:
        # an agent holding the speak lock is talking right now, whatever the state
        # file says - and it is worth showing even with no daemon running at all
        speaking = SPEAK_LOCK.exists()
        if not daemon_running():
            return "speaking" if speaking else "off"
        if speaking:
            return "speaking"
        try:
            state = STATE_FILE.read_text().strip()
        except OSError:
            return "idle"
        return state if state in STATES else "idle"

    @objc.python_method
    def read_owner(self) -> str:
        try:
            return OWNER_FILE.read_text().split("\n")[0].strip()
        except OSError:
            return ""

    def poll_(self, _timer):
        state = self.read_state()
        owner = self.read_owner()
        if self.shown:
            self.remember_spot()
        if state == self.state and owner == self.owner:
            return
        was = self.state
        self.state, self.owner = state, owner
        visible = state in STATES and (SHOW_IDLE or state != "idle")
        print(f"capsule: {state} -> {'showing' if visible else 'hiding'}", flush=True)
        if not visible:
            self.stop_bars()
            if self.shown:
                self.panel.orderOut_(None)
                self.shown = False
            return
        self.apply(state, owner, waking=(was in (None, "idle") and state != "idle"))
        if not self.shown:
            self.place()
            self.panel.orderFrontRegardless()
            self.shown = True

    @objc.python_method
    def apply(self, state, owner, waking):
        _, rgb, glow, dur_a, dur_b, dur_pip, viz_w = STATES[state]
        label = badge_word(state)
        hue = hue_color(rgb)

        text = label_string(label, owner if state != "idle" else "")
        text_w = math.ceil(text.size().width) if state != "idle" else 0
        width = COLLAPSED_W if state == "idle" else (
            PAD + CORE + GAP_LABEL + text_w + GAP_VIZ + viz_w + PAD_RIGHT + PAD)

        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.5)

        # colours follow the hue everywhere at once
        for s in (self.static_ring, self.pip_ring, *self.ring_a_arcs, *self.ring_b_arcs):
            s.setStrokeColor_(hue)
        self.pip.setFillColor_(hue)
        for b in self.bars:
            b.setBackgroundColor_(hue)
        for d in self.swarm_dots:
            d.setBackgroundColor_(hue)
        self.wave_axis.setStrokeColor_(hue)
        self.wave_trace.setStrokeColor_(hue)
        for l in self.swarm_links:
            l.setStrokeColor_(hue)
        self.capsule.setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.06 if state == "idle" else 0.10).CGColor())
        self.capsule.setOpacity_(0.58 if state == "idle" else 1.0)
        self.glow.setShadowColor_(hue)
        self.glow.setShadowOpacity_(glow * 0.5)
        self.glow.setShadowRadius_(4 + glow * 6)

        # geometry: capsule width springs, glow path follows
        self.cap_w = width
        self.capsule.setBounds_(CGRectMake(0, 0, width, CAPSULE_H))
        right = self.capsule.position().x
        cap_y = (WIN_H - CAPSULE_H) / 2.0
        self.glow.setFrame_(CGRectMake(right - width, cap_y, width, CAPSULE_H))
        self.glow.setShadowPath_(
            CGPathCreateWithRoundedRect(CGRectMake(0, 0, width, CAPSULE_H),
                                        CAPSULE_H / 2.0, CAPSULE_H / 2.0, None))

        # body: label and viz frames inside the capsule
        self.label.setString_(text)
        self.label.setFrame_(CGRectMake(PAD + CORE + GAP_LABEL,
                                        (CAPSULE_H - 15) / 2.0, max(text_w, 1), 15))
        self.label.setOpacity_(0.0 if state == "idle" else 1.0)
        viz_x = PAD + CORE + GAP_LABEL + text_w + GAP_VIZ
        self.viz.setFrame_(CGRectMake(viz_x, (CAPSULE_H - VIZ_H) / 2.0, viz_w, VIZ_H))
        self.swarm.setFrame_(CGRectMake(viz_x, (CAPSULE_H - VIZ_H) / 2.0, viz_w, VIZ_H))
        self.wave.setFrame_(CGRectMake(viz_x, (CAPSULE_H - VIZ_H) / 2.0, viz_w, VIZ_H))
        # the bars are gone from every state now: listening and speaking both draw
        # a real signal, thinking draws the swarm
        self.viz.setHidden_(True)
        self.swarm.setHidden_(state != "thinking")
        self.wave.setHidden_(state not in ("listening", "speaking"))
        if state in ("listening", "speaking"):
            axis = CGPathCreateMutable()
            CGPathMoveToPoint(axis, None, 0, WAVE_MID)
            CGPathAddLineToPoint(axis, None, viz_w, WAVE_MID)
            self.wave_axis.setPath_(axis)

        CATransaction.commit()

        self.spin(self.ring_a, dur_a)
        self.spin(self.ring_b, dur_b, reverse=True)
        for l in (self.pip, self.pip_ring):
            l.removeAllAnimations()
        self.breathe(self.pip, dur_pip)
        self.sheen.setColors_([hue_color(rgb, 0.0), hue_color(rgb, 0.26), hue_color(rgb, 0.0)])
        # the shimmer sweep is gone: thinking-swarm.html has no sheen, the swarm
        # is the whole of the thinking animation now
        self.sheen_sweep(False, width)
        if waking:
            self.wake_pop()

        if state == "speaking":
            self.speak_started = time.monotonic()
        if state == "idle":
            self.stop_bars()
        else:
            self.layout_bars(viz_w)
            self.start_bars(state)

    # ── equalizer (the only python-driven animation, active states only) ───────

    @objc.python_method
    def layout_bars(self, viz_w):
        self.viz_scale = viz_w / 100.0
        for i, b in enumerate(self.bars):
            b.setCornerRadius_(2.2 * self.viz_scale)

    @objc.python_method
    def start_bars(self, state):
        """The swarm and the equalizer run at different rates, so the timer is
        rebuilt whenever the needed interval changes."""
        want = 1.0 / (SWARM_FPS if state == "thinking"
                      else WAVE_FPS if state in ("listening", "speaking") else FPS)
        if self.bars_timer is not None and abs(self.bars_interval - want) < 1e-6:
            return
        self.stop_bars()
        self.bars_interval = want
        self.bars_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            want, self, "bars:", None, True)

    @objc.python_method
    def stop_bars(self):
        if self.bars_timer is not None:
            self.bars_timer.invalidate()
            self.bars_timer = None

    def bars_(self, _timer):
        self.tick += 1
        state = self.state
        if state == "thinking":
            self.draw_swarm()
            return
        if state in ("listening", "speaking"):
            self.draw_wave()
            return
        if state == "listening" and self.tick % 2 == 0:
            centre = (BAR_COUNT - 1) / 2.0
            for i in range(BAR_COUNT):
                c = 1 - abs(i - centre) / centre
                self.targets[i] = 0.1 + (random.random() ** 1.6) * (0.35 + c * 0.65)

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        s = self.viz_scale
        for i, b in enumerate(self.bars):
            if state == "thinking":
                target = 0.16 + max(0.0, math.sin(self.tick / 4.5 - i * 0.55)) * 0.62
            elif state == "speaking":
                age = time.monotonic() - self.speak_started
                calm = max(0.34, 1 - (age - 5) / 12) if age > 5 else 1.0
                target = 0.2 + (math.sin(self.tick / 3.0 - i * 0.42) * 0.5 + 0.5) * 0.66 * calm
            elif state == "listening":
                target = self.targets[i]
            else:
                target = 0.06
            self.levels[i] += (target - self.levels[i]) * (0.4 if state == "listening" else 0.3)
            h = max(3.0, self.levels[i] * VIZ_H)
            x = (i * 9 + 1) * s
            b.setFrame_(CGRectMake(x, (VIZ_H - h) / 2.0, 4.4 * s, h))
            b.setOpacity_(0.42 + self.levels[i] * 0.58)
        CATransaction.commit()


    @objc.python_method
    def draw_swarm(self):
        """One frame of the thinking swarm: move the dots, re-link the near ones.

        Straight port of the mockup's frame(): the clock is milliseconds since the
        process started, which is what its requestAnimationFrame timestamp was. The
        y axis is flipped because the mockup draws in svg coordinates and a layer
        counts upwards.
        """
        now = (time.monotonic() - self.swarm_t0) * 1000.0
        pos = []
        for seed in self.swarm_seeds:
            pos.append((seed["ax"] + math.cos(now * seed["sx"] + seed["px"]) * seed["rx"],
                        seed["ay"] + math.sin(now * seed["sy"] + seed["py"]) * seed["ry"]))
        heat = [0.0] * SWARM_DOTS

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        used = 0
        for a in range(SWARM_DOTS):
            if used >= SWARM_MAX_LINKS:
                break
            for b in range(a + 1, SWARM_DOTS):
                if used >= SWARM_MAX_LINKS:
                    break
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                dist = math.hypot(dx, dy)
                if dist > SWARM_LINK:
                    continue
                f = (1 - dist / SWARM_LINK) ** SWARM_FADE
                heat[a] += f
                heat[b] += f
                # bow the link so nothing reads as a straight rule
                mx = (pos[a][0] + pos[b][0]) / 2.0 - dy * SWARM_BOW
                my = (pos[a][1] + pos[b][1]) / 2.0 + dx * SWARM_BOW
                path = CGPathCreateMutable()
                CGPathMoveToPoint(path, None, pos[a][0], VIZ_H - pos[a][1])
                CGPathAddQuadCurveToPoint(path, None, mx, VIZ_H - my,
                                          pos[b][0], VIZ_H - pos[b][1])
                link = self.swarm_links[used]
                link.setPath_(path)
                link.setLineWidth_(SWARM_LINK_W * (0.55 + f * 0.45))
                link.setOpacity_(min(1.0, f * 1.15))
                used += 1
        for spare in self.swarm_links[used:]:
            spare.setOpacity_(0.0)

        for i, dot in enumerate(self.swarm_dots):
            h = min(1.0, heat[i] / 2.2)
            r = SWARM_DOT_R + h * 0.7
            dot.setFrame_(CGRectMake(pos[i][0] - r, VIZ_H - pos[i][1] - r, r * 2, r * 2))
            dot.setCornerRadius_(r)
            dot.setOpacity_(0.6 + h * 0.4)
        CATransaction.commit()


    @objc.python_method
    def capsule_screen_rect(self):
        """Where the capsule really is on screen, in points."""
        f = self.panel.frame()
        right = f.origin.x + WIN_W - SHADOW_ROOM
        return (right - self.cap_w, f.origin.y + SHADOW_ROOM,
                self.cap_w, CAPSULE_H)

    def hit_(self, _timer):
        """Catch the mouse only over the capsule, let the shadow area through."""
        if not DRAGGABLE or not self.shown:
            if self.catching:
                self.panel.setIgnoresMouseEvents_(True)
                self.catching = False
            return
        if NSEvent.pressedMouseButtons() and self.catching:
            return                      # a drag is in progress, do not let go
        p = NSEvent.mouseLocation()
        x, y, w, h = self.capsule_screen_rect()
        inside = x <= p.x <= x + w and y <= p.y <= y + h
        if inside != self.catching:
            self.panel.setIgnoresMouseEvents_(not inside)
            self.catching = inside


    @objc.python_method
    def read_level(self):
        """Pick up whatever the daemon has published since the last look."""
        try:
            with open(LEVEL_FILE) as f:     # plain open: pathlib costs more per call
                parts = f.read().split()
            seq = int(parts[0])
        except (OSError, ValueError, IndexError):
            return
        if seq == self.wave_seq:
            return
        self.wave_seq = seq
        try:
            fresh = [float(x) for x in parts[1:]]
        except ValueError:
            return
        self.wave_queue.extend(fresh)
        # a stall - a locked screen, a sleeping laptop - must not replay a backlog
        if len(self.wave_queue) > WAVE_SAMPLES:
            self.wave_queue = self.wave_queue[-WAVE_SAMPLES:]

    @objc.python_method
    def speak_sample(self):
        """Where the phrase being spoken is right now, or None if none is."""
        try:
            mtime = os.path.getmtime(SPEAK_LEVEL_FILE)
        except OSError:
            return None
        if mtime != self.speak_mtime:
            self.speak_mtime = mtime
            try:
                with open(SPEAK_LEVEL_FILE) as f:
                    parts = f.read().split()
                self.speak_start = float(parts[0])
                self.speak_rate = float(parts[1]) or 32.0
                self.speak_env = [float(x) for x in parts[2:]]
            except (OSError, ValueError, IndexError):
                self.speak_env = []
        if not self.speak_env:
            return None
        i = int((time.time() - self.speak_start) * self.speak_rate)
        if i < 0 or i >= len(self.speak_env):
            return None
        return self.speak_env[i]

    @objc.python_method
    def draw_wave(self):
        """One frame of the listening trace: consume a sample, redraw the line.

        Straight port of the mockup's frame(). The signed peak comes from the
        daemon rather than from a WebAudio analyser, which is the same idea: keep
        the envelope, not the raw waveform, so syllables are visible.
        """
        if self.state == "speaking":
            spoken = self.speak_sample()
            if spoken is None:
                raw = 0.0
                self.wave_level *= 0.9
            else:
                raw = spoken
                self.wave_level = abs(spoken)
            self.push_wave(raw)
            return
        self.read_level()
        if self.wave_queue:
            raw = self.wave_queue.pop(0)
            self.wave_level = abs(raw)
        else:
            # nothing new: relax towards the axis so a dead mic reads as quiet
            raw = 0.0
            self.wave_level *= 0.9
        self.push_wave(raw)

    @objc.python_method
    def push_wave(self, raw):
        """One sample into the trace, then redraw it."""
        # idle wobble so a silent line still reads as alive, not frozen
        raw += (math.sin(self.tick * 0.12) * WAVE_IDLE_WOB
                * (1 - min(1.0, self.wave_level * 4)))
        self.wave_prev = self.wave_prev * WAVE_SMOOTH + raw * (1 - WAVE_SMOOTH)
        self.wave_buf.pop(0)
        self.wave_buf.append(self.wave_prev)

        w = self.wave.bounds().size.width or WAVE_W
        path = CGPathCreateMutable()
        px = py = None
        for i, v in enumerate(self.wave_buf):
            x = (i / (WAVE_SAMPLES - 1)) * w
            y = WAVE_MID + v * WAVE_AMP      # layer y counts upwards
            if px is None:
                CGPathMoveToPoint(path, None, x, y)
            else:
                CGPathAddQuadCurveToPoint(path, None, px, py,
                                          (px + x) / 2.0, (py + y) / 2.0)
            px, py = x, y
        CGPathAddLineToPoint(path, None, w, py)

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self.wave_trace.setPath_(path)
        self.wave_trace.setOpacity_(0.55 + min(1.0, self.wave_level) * 0.45)
        CATransaction.commit()


app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
capsule = Capsule.alloc().init()
NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
    0.25, capsule, "poll:", None, True)
if DRAGGABLE:
    # only armed when dragging is wanted: with the window click-through from the
    # start there is nothing to switch, and no reason to wake up for the cursor
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        HIT_POLL, capsule, "hit:", None, True)
print("Jarvis overlay (capsule) running. Ctrl+C to stop.")
app.run()
