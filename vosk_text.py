"""Turn anything Claude says into text the local voice can actually pronounce.

The vosk model knows Cyrillic letters and a handful of punctuation marks. A digit,
a latin letter or a «ёлочка» raises KeyError deep inside the library, and the
phrase is lost with no sound at all - which is exactly how the answer about
a surname vanished mid-sentence on 20.08. Everything unknown is converted or
dropped here.
"""
import re

UNITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
UNITS_F = ["ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
         "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
        "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
            "семьсот", "восемьсот", "девятьсот"]
SCALES = [("", "", "", False),
          ("тысяча", "тысячи", "тысяч", True),
          ("миллион", "миллиона", "миллионов", False),
          ("миллиард", "миллиарда", "миллиардов", False)]

# Latin words that show up in every second sentence at work
LATIN_WORDS = {
    "merge": "мёрдж", "request": "реквест", "pull": "пул", "commit": "коммит",
    "deploy": "деплой", "pipeline": "пайплайн", "build": "билд", "release": "релиз",
    "review": "ревью", "branch": "бранч", "master": "мастер", "main": "мейн",
    "gitlab": "гитлаб", "github": "гитхаб", "jira": "джира", "claude": "клод",
    "keycloak": "кейклоак", "redis": "редис", "docker": "докер", "test": "тэст",
    "tests": "тэсты", "lint": "линт", "linter": "линтер", "ok": "окей",
    # added 21.08 after the owner listened to a whole day of speech: without an entry
    # here a latin word falls through to TRANSLIT letter by letter, and "remote"
    # came out as "ремоте"
    "remote": "ремоут", "staging": "стэйджинг", "stage": "стэйдж",
    "referral": "реферал", "referrer": "реферер", "tag": "тэг", "tags": "тэги",
    "legacy": "легаси", "prod": "прод", "production": "прод",
    "merged": "смёрджено", "push": "пуш", "pushed": "запушено",
    "feature": "фича", "popup": "попап", "polling": "поллинг",
    "websocket": "вебсокет", "webhook": "вебхук", "signoz": "сигноз",
    "daily": "дейлик", "vitest": "витест", "jest": "джест", "storybook": "сторибук",
    "snapshot": "снапшот", "rollback": "роллбэк", "hotfix": "хотфикс",
    "ready": "рэди", "testing": "тэстинг", "backend": "бэкенд", "frontend": "фронтенд",
    # field names read the English way, not letter by letter: has_deposits was
    # coming out as "хас депоситс"
    "has": "хэз", "deposit": "дипозит", "deposits": "дипозитс",
    "priority": "приорити", "critical": "критикал", "blocker": "блокер",
    "major": "мэйджор", "minor": "майнор", "thread": "тред", "threads": "треды",
    "unit": "юнит", "units": "юниты",
    "enabled": "инэйблд", "disabled": "дисэйблд", "count": "каунт",
}
# how the owner says these out loud, not how a dictionary would
ACRONYMS = {
    "roc": "эр о си", "mr": "эм эр", "ci": "си ай", "cd": "си ди",
    "api": "эй пи ай", "qa": "кью эй", "pr": "пи эр", "ssr": "эс эс эр",
    "sso": "эс эс о", "url": "юар эл", "id": "айди", "ui": "ю ай",
    "qa": "кьюэй", "dpop": "дипоп", "xss": "икс эс эс", "qs": "кьюэс",
    "bf": "бэ эф", "sup": "суп", "devops": "девопс", "http": "эйч ти ти пи",
}
LETTER_NAMES = {
    "a": "а", "b": "бэ", "c": "цэ", "d": "дэ", "e": "е", "f": "эф", "g": "гэ",
    "h": "аш", "i": "и", "j": "йот", "k": "ка", "l": "эль", "m": "эм",
    "n": "эн", "o": "о", "p": "пэ", "q": "ку", "r": "эр", "s": "эс",
    "t": "тэ", "u": "у", "v": "вэ", "w": "дубль вэ", "x": "икс", "y": "игрек", "z": "зет",
}
TRANSLIT = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
            "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
            "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
            "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з"}

REPLACEMENTS = {
    "«": "", "»": "", "\"": "", "„": "", "“": "", "”": "", "‘": "", "’": "",
    "…": ".", "№": "номер ", "%": " процентов", "&": " и ", "@": " собака ",
    "/": " ", "\\": " ", "*": "", "#": "", "_": " ", "|": " ", "+": " плюс ",
    "=": " равно ", ">": " ", "<": " ", "[": "(", "]": ")", "{": "(", "}": ")",
    "$": " долларов", "€": " евро", "~": "", "^": "", "`": "",
}
ALLOWED_PUNCT = set(" .,!?;:-()")


def _triple(n: int, feminine: bool) -> list[str]:
    words = []
    if n >= 100:
        words.append(HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        words.append(TENS[n // 10])
        n %= 10
    elif n >= 10:
        words.append(TEENS[n - 10])
        n = 0
    if n:
        words.append((UNITS_F if feminine else UNITS)[n])
    return words


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 100 // 10 == 1:
        return many
    last = n % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def number_to_words(number: int) -> str:
    """15847 -> «пятнадцать тысяч восемьсот сорок семь»."""
    if number == 0:
        return "ноль"
    sign = "минус " if number < 0 else ""
    number = abs(number)
    groups = []
    while number:
        groups.append(number % 1000)
        number //= 1000
    words: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        value = groups[idx]
        if not value:
            continue
        one, few, many, feminine = SCALES[idx] if idx < len(SCALES) else SCALES[-1]
        words += _triple(value, feminine)
        if one:
            words.append(_plural(value, one, few, many))
    return sign + " ".join(words)


def _numbers(text: str) -> str:
    def sub(match: "re.Match[str]") -> str:
        whole = match.group(0)
        if len(whole) > 12:  # a hash or an id, read it digit by digit
            return " ".join(UNITS[int(d)] for d in whole)
        return number_to_words(int(whole))
    text = re.sub(r"(\d+)[.,](\d+)",
                  lambda m: f"{number_to_words(int(m.group(1)))} и {number_to_words(int(m.group(2)))}",
                  text)
    return re.sub(r"\d+", sub, text)


def _latin(text: str) -> str:
    def sub(match: "re.Match[str]") -> str:
        word = match.group(0)
        known = ACRONYMS.get(word.lower()) or LATIN_WORDS.get(word.lower())
        if known:
            return known
        if word.isupper() and len(word) <= 4:      # ROC, MR, CI - read as letters
            return " ".join(LETTER_NAMES.get(ch.lower(), ch) for ch in word)
        return "".join(TRANSLIT.get(ch.lower(), "") for ch in word)
    return re.sub(r"[A-Za-z]+", sub, text)


def normalize_for_tts(text: str) -> str:
    """Everything the model cannot pronounce is converted or thrown away."""
    for src, dst in REPLACEMENTS.items():
        text = text.replace(src, dst)
    text = text.replace("—", "-").replace("–", "-").replace("−", "-")
    text = _numbers(text)
    text = _latin(text)
    kept = [ch for ch in text if ch.isalpha() and ("а" <= ch.lower() <= "я" or ch in "ёЁ")
            or ch in ALLOWED_PUNCT]
    text = "".join(kept)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([.,!?;:])\1+", r"\1", text)
    return text


if __name__ == "__main__":
    import sys
    print(normalize_for_tts(" ".join(sys.argv[1:])))
