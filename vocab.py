"""Reuse the owner's FluidVoice dictionary to clean up what parakeet heard.

FluidVoice applies its own vocabulary boosting on top of the model; the
parakeet-mlx CLI has no such feature, so the same corrections are applied here
afterwards. Two sources, both already maintained by hand in FluidVoice:
  ~/Library/Application Support/FluidVoice/parakeet_custom_vocabulary.json
      terms with weights - matched fuzzily against single words
  defaults read com.FluidApp.app CustomDictionaryEntries
      explicit trigger -> replacement pairs
"""
import difflib
import json
import os
import plistlib
import re
import subprocess

VOCAB_FILE = os.path.expanduser(
    "~/Library/Application Support/FluidVoice/parakeet_custom_vocabulary.json")
MIN_SIMILARITY = 0.82   # below this a word is left alone; FluidVoice uses 0.72
MIN_LEN = 4             # short words give too many false corrections


def _load_terms() -> list[str]:
    try:
        with open(VOCAB_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    terms = []
    for item in data.get("terms", []):
        text = item.get("text", "").strip()
        if text:
            terms.append(text)
        terms.extend(a.strip() for a in item.get("aliases", []) if a.strip())
    return terms


def _load_replacements() -> list[tuple[str, str]]:
    try:
        out = subprocess.run(["defaults", "export", "com.FluidApp.app", "-"],
                             capture_output=True, timeout=10).stdout
        raw = plistlib.loads(out).get("CustomDictionaryEntries")
        entries = json.loads(raw.decode()) if isinstance(raw, bytes) else raw or []
    except Exception:
        return []
    pairs = []
    for e in entries:
        target = (e.get("replacement") or "").strip()
        if not target:
            continue
        for trigger in e.get("triggers", []):
            for part in str(trigger).split(","):
                part = part.strip()
                if part and part.lower() != target.lower():
                    pairs.append((part, target))
    # longest first, so "мерж реквест" wins over "мерж"
    return sorted(pairs, key=lambda p: -len(p[0]))


TERMS = _load_terms()
REPLACEMENTS = _load_replacements()
WORD = re.compile(r"[\w-]+", re.UNICODE)


def correct(text: str) -> str:
    if not text:
        return text
    fixed = text
    for trigger, target in REPLACEMENTS:
        fixed = re.sub(r"(?i)\b" + re.escape(trigger) + r"\b", target, fixed)

    if not TERMS:
        return fixed

    def fix_word(m: re.Match) -> str:
        word = m.group(0)
        if len(word) < MIN_LEN or word in TERMS:
            return word
        best = difflib.get_close_matches(word.lower(),
                                         [t.lower() for t in TERMS],
                                         n=1, cutoff=MIN_SIMILARITY)
        if not best:
            return word
        for term in TERMS:  # restore the original capitalisation of the term
            if term.lower() == best[0]:
                return term
        return word

    return WORD.sub(fix_word, fixed)


if __name__ == "__main__":
    print(f"terms: {len(TERMS)}, replacements: {len(REPLACEMENTS)}")
    for probe in ["сделай ревью мерш реквест 15900",
                  "проверь pipeline и сентри",
                  "посмотри джобу в гитлабе",
                  "погода в валенсии завтра"]:
        print(f"  {probe!r} -> {correct(probe)!r}")
