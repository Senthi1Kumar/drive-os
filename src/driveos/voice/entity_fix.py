"""Post-ASR repair of place and menu names.

Parakeet cannot be biased with a prompt the way Whisper can, so a misheard "boba guys" arrives as "bob a guys".
This fuzzy-matches runs of words against the live vocabulary and replaces only spans that closely match a known
entity. It never touches digits or number words, and callers skip it entirely while a one-time code is pending.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

THRESHOLD = 88.0          # rapidfuzz ratio (0-100) on the space-free lowercase strings
MIN_COMPACT = 5           # shorter names are too easy to confuse with ordinary words
_WORD = re.compile(r"[A-Za-z0-9']+")
_NUMBERS = frozenset("""zero oh one two three four five six seven eight nine ten eleven twelve thirteen fourteen
fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand
double triple to too for won ate""".split())
# the fixed command words at the head of Nova.vocabulary(); matching these fuzzily would rewrite ordinary speech
GENERIC = frozenset({"nova", "driveos", "coffee", "sandwich", "gas station", "face id", "one-time code",
                     "confirm", "check out", "take me there"})


# a span never starts or ends on one of these, so "Creamery on Hamilton" keeps its "on"
_FUNCTION = frozenset("a an the to on in at of and or for from near by with my me is it please".split())


def _compact(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def entities_from_vocabulary(vocabulary: str) -> list[str]:
    """Entity names in a comma-joined vocabulary, minus the fixed command words."""
    out, seen = [], set()
    for part in vocabulary.split(","):
        name = part.strip()
        key = name.lower()
        if not name or key in GENERIC or key in seen or len(_compact(name)) < MIN_COMPACT:
            continue
        seen.add(key)
        out.append(name)
    return out


def _is_numeric(tok: str) -> bool:
    return any(c.isdigit() for c in tok) or tok.lower() in _NUMBERS


def fix_entities(text: str, entities: list[str], *, threshold: float = THRESHOLD) -> str:
    """Replace word runs that closely match an entity name with its canonical spelling."""
    if not text or not entities:
        return text
    toks = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    if not toks:
        return text
    prepared = [(e, _compact(e), len(e.split())) for e in entities]
    cands: list[tuple[float, int, int, int, str]] = []   # (score, length, start, end, name)
    for name, comp, n_words in prepared:
        for n in {max(1, n_words - 1), n_words, n_words + 1}:
            for i in range(len(toks) - n + 1):
                window = toks[i:i + n]
                if any(_is_numeric(w[0]) for w in window):
                    continue
                if window[0][0].lower() in _FUNCTION or window[-1][0].lower() in _FUNCTION:
                    continue
                got = _compact(" ".join(w[0] for w in window))
                if not got or got[0] != comp[0] or abs(len(got) - len(comp)) > max(2, len(comp) // 4):
                    continue
                if got == comp and " ".join(w[0] for w in window).lower() == name.lower():
                    continue                              # already spelled right
                if got in (comp + "s", comp + "es") or comp in (got + "s", got + "es"):
                    continue                              # plural or singular of the name, not a mishearing
                score = fuzz.ratio(got, comp)
                if score >= threshold:
                    cands.append((score, n, i, i + n, name))
    cands.sort(key=lambda c: (-c[0], -c[1], c[2]))
    taken: list[tuple[int, int, str]] = []
    used: set[int] = set()
    for _, _, a, b, name in cands:
        if used.intersection(range(a, b)):
            continue
        used.update(range(a, b))
        taken.append((a, b, name))
    out, pos = [], 0
    for a, b, name in sorted(taken):
        out.append(text[pos:toks[a][1]])
        out.append(name)
        pos = toks[b - 1][2]
    out.append(text[pos:])
    return "".join(out)
