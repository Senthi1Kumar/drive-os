"""Challenge liveness: a random digit string spoken on demand, checked by ASR, plus a two-channel sanity check."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

import numpy as np

_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SAY = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def generate_challenge(n: int = 4) -> str:
    """Random digits with no repeated digit and no adjacent (consecutive) digits, so ASR confusions stay rare."""
    while True:
        ds = [secrets.randbelow(10) for _ in range(n)]
        if len(set(ds)) == n and all(abs(a - b) != 1 for a, b in zip(ds, ds[1:], strict=False)):
            return "".join(map(str, ds))


def spoken_digits(digits: str) -> str:
    return " ".join(_SAY[int(c)] for c in digits)


def digits_from_text(text: str) -> str:
    """"four two seven nine", "4279", "4 2 7 9", "forty-two seventy-nine" -> "4279"."""
    toks = re.findall(r"[a-z]+|\d+", text.lower().replace("-", " "))
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isdigit():
            out.append(t)
        elif t in _TENS:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt in _UNITS and _UNITS[nxt] > 0:
                out.append(str(_TENS[t] + _UNITS[nxt]))
                i += 1
            else:
                out.append(str(_TENS[t]))
        elif t in _TEENS:
            out.append(str(_TEENS[t]))
        elif t in _UNITS:
            out.append(str(_UNITS[t]))
        i += 1
    return "".join(out)


# Spoken as plain digit groups so the recogniser writes digits, not "four thousand two hundred".
DIGIT_PROMPT = "Digits: 4 7 2 9. 0 1 3 5. 8 6 2 4. 9 3 1 7."


def challenge_matches(text: str, digits: str) -> bool:
    """Digit words and numerals, plus the spoken-code parser's homophones ("for", "to", "oh", "double five")."""
    if digits_from_text(text) == digits:
        return True
    from ..agent.intents import parse_code

    return parse_code(text, len(digits)) == digits


@dataclass
class ChannelCheck:
    status: str          # "pass" | "fail" | "n/a"
    detail: str = ""


def channel_check(ch0: np.ndarray | None, ch1: np.ndarray | None, corr_max: float = 0.9999) -> ChannelCheck:
    """Fail when the two capture channels are bit-identical or perfectly correlated at zero lag (a duplicated
    mono stream, typical of injected audio). One-channel devices report n/a, never a failure."""
    if ch0 is None or ch1 is None:
        return ChannelCheck("n/a", "single-channel capture")
    n = min(len(ch0), len(ch1))
    if n < 1600:
        return ChannelCheck("n/a", "capture too short")
    a, b = np.asarray(ch0[:n], dtype=np.float64), np.asarray(ch1[:n], dtype=np.float64)
    if np.array_equal(a, b):
        return ChannelCheck("fail", "channels are bit-identical")
    if a.std() < 1e-6 or b.std() < 1e-6:
        return ChannelCheck("n/a", "silent channel")
    r = float(np.corrcoef(a, b)[0, 1])
    if r >= corr_max:
        return ChannelCheck("fail", f"channels correlate {r:.5f} at zero lag")
    return ChannelCheck("pass", f"channel correlation {r:.3f}")
