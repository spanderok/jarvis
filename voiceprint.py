"""Is this the owner talking, or someone else in the room?

One utterance, end to end:
  1. 16 kHz int16 audio comes in - the same array `record()` already built.
  2. kaldi-native-fbank turns it into 80-dim log-mel frames, 25 ms window,
     10 ms shift, exactly the recipe CAM++ was trained on.
  3. campplus.onnx folds the frames into one 512-number vector - the voice
     print of whoever spoke.
  4. That vector is compared with two crowds: every take recorded during
     enrollment, and a cohort of voices that are not his. Two numbers come
     out - how much the phrase looks like him, and how much it looks like
     somebody else - and he has to win by a margin.

Why two numbers and not one threshold. Measured on the real profile on 22.08:
the synthesized Jarvis voice scored 0.65 against the owner's own neutral take,
while his tired take scored 0.57 against his other takes. One line cannot
separate those. But the same synthesized clip scores 0.95 against other clips
of itself, so "looks like him minus looks like the cohort" lands at -0.31 for
it and at +0.10 or better for every take of his. The inflation this model gives
to male voices sits in both terms and cancels out.

Everything fails open. No model, no profile, broken onnx, audio too short -
`check()` returns "let him through". A voice lock that silences Jarvis because
a file went missing would be worse than no lock at all.
"""

import json
import os
import threading

import numpy as np

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(JARVIS_DIR, "models", "campplus.onnx")
PROFILE = os.path.join(JARVIS_DIR, "voiceprint.json")

SAMPLE_RATE = 16000

# Below this much audio the embedding is noise: the vector is built from the
# whole phrase, and one short word does not carry enough voice in it. Measured
# on the enrollment set - see enroll_voice.py, which prints the same number for
# 0.5 s / 1 s / 2 s cuts of the same phrase.
MIN_SEC = float(os.environ.get("JARVIS_SPK_MIN_SEC", "1.0"))

# off = never check. Useful when the microphone changes and the profile has not
# been re-recorded yet.
ENABLED = os.environ.get("JARVIS_SPK", "on").lower() not in ("off", "0", "no")

_lock = threading.Lock()
_session = None
_profile = None
_tried_model = False
_profile_mtime = None


def _load_model() -> None:
    """Bring up onnx once, on the first phrase, not at boot.

    Separate from the profile on purpose: enrollment needs the model while the
    profile does not exist yet.
    """
    global _session, _tried_model
    if _tried_model:
        return
    _tried_model = True
    if not os.path.exists(MODEL):
        return
    try:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        _session = ort.InferenceSession(MODEL, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
    except Exception:
        _session = None


def _load_profile() -> None:
    """Read the profile, and read it again when it changes on disk.

    Latching on the first look would mean a daemon started before enrollment
    never sees the profile that appears five minutes later - and re-recording a
    take would need a restart to take effect.
    """
    global _profile, _profile_mtime
    try:
        mtime = os.path.getmtime(PROFILE)
    except OSError:
        _profile = None
        _profile_mtime = None
        return
    if _profile is not None and mtime == _profile_mtime:
        return
    _profile_mtime = mtime
    try:
        with open(PROFILE, encoding="utf-8") as fh:
            data = json.load(fh)
        vecs = np.asarray(data["samples"], dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        cohort = np.asarray(data.get("cohort", []), dtype=np.float32)
        if len(cohort):
            cohort /= np.linalg.norm(cohort, axis=1, keepdims=True)
        _profile = {
            "vecs": vecs,
            "labels": data.get("labels", [""] * len(vecs)),
            "cohort": cohort,
            "floor": float(data["floor"]),
            "margin": float(data["margin"]),
        }
    except Exception:
        _profile = None


def fbank(audio: np.ndarray) -> np.ndarray:
    """80-dim log-mel frames, mean-normalised over time, as CAM++ wants them."""
    import kaldi_native_fbank as knf
    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = float(SAMPLE_RATE)
    opts.frame_opts.dither = 0.0
    opts.frame_opts.snip_edges = True
    opts.mel_opts.num_bins = 80
    ext = knf.OnlineFbank(opts)
    # kaldi wants samples on the int16 scale, which is what the microphone
    # already gives us - no division by 32768 anywhere in this file.
    ext.accept_waveform(float(SAMPLE_RATE), audio.astype(np.float32).tolist())
    ext.input_finished()
    n = ext.num_frames_ready
    if n < 8:
        raise ValueError("too few frames")
    feats = np.stack([ext.get_frame(i) for i in range(n)]).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def embed(audio: np.ndarray) -> np.ndarray:
    """One utterance -> one 512-number unit vector."""
    _load_model()
    if _session is None:
        raise RuntimeError("speaker model is not loaded")
    feats = fbank(audio)[None]
    vec = _session.run(None, {"feats": feats})[0][0].astype(np.float32)
    return vec / (np.linalg.norm(vec) + 1e-9)


def check(audio: np.ndarray) -> tuple[bool, float, str]:
    """(is it him, best match 0..1, why).

    The reason is for the log, and it is also what decides whether Jarvis says
    anything out loud: only a real "someone else" earns the refusal, a missing
    profile does not.
    """
    if not ENABLED:
        return True, 1.0, "off"
    secs = len(audio) / SAMPLE_RATE
    if secs < MIN_SEC:
        return True, 1.0, f"too short ({secs:.1f}s)"
    with _lock:
        _load_model()
        _load_profile()
        if _session is None or _profile is None:
            return True, 1.0, "no profile"
        try:
            vec = embed(audio)
        except Exception as e:
            return True, 1.0, f"embed failed: {e}"
        scores = _profile["vecs"] @ vec
        best = int(np.argmax(scores))
        mine = float(scores[best])
        label = _profile["labels"][best]
        cohort = _profile["cohort"]
        theirs = float(np.max(cohort @ vec)) if len(cohort) else 0.0
        floor, margin = _profile["floor"], _profile["margin"]
    gap = mine - theirs
    if mine < floor:
        return (False, mine,
                f"not his voice (against him {mine:.2f} < {floor:.2f}, "
                f"against the cohort {theirs:.2f}, margin {gap:+.2f})")
    if gap < margin:
        return (False, mine,
                f"not his voice (against him {mine:.2f}, but against the cohort "
                f"{theirs:.2f}, margin {gap:+.2f} < {margin:+.2f})")
    return (True, mine,
            f"his voice ({mine:.2f} as {label!r}, cohort {theirs:.2f}, "
            f"margin {gap:+.2f})")


def warmup() -> None:
    """Load onnx and the profile ahead of time, in a thread, at boot.

    First use costs about half a second - the model is 28 MB. Paying it while
    Jarvis is still waking up means his first phrase of the day is not slower
    than the rest.
    """
    if not ENABLED:
        return
    def work() -> None:
        try:
            with _lock:
                _load_model()
                _load_profile()
                if _session is not None and _profile is not None:
                    embed(np.zeros(SAMPLE_RATE, dtype=np.int16))
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
