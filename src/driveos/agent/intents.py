"""On-device intent parser for in-car commerce.

Deterministic and fast (no network), so the core flow — find, choose, add, check out, approve, drive —
always works. The language-model agent (``agent/harness``), when enabled, handles open-ended phrasing; these rules still
take the safety-critical turns (approvals, codes, cancellations) so consent never depends on a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process, utils

from ..domain.categories import detect
from ..domain.models import Menu, MenuItem

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "couple": 2, "a couple": 2, "double": 2, "single": 1,
             "to": 2, "too": 2, "for": 4}  # STT homophones only used next to an item
DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
               "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8",
               "nine": "9"}
ORDINALS = {"first": 0, "1st": 0, "one": 0, "top": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2,
            "three": 2, "fourth": 3, "4th": 3, "four": 3, "fifth": 4, "5th": 4, "five": 4, "sixth": 5,
            "six": 5, "last": -1, "closest": 0, "nearest": 0}

YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "do it", "go ahead",
       "place it", "place the order", "place order", "approve", "approved", "sounds good", "correct", "right",
       "that's right", "please do", "go for it", "send it", "pay", "pay now", "buy it")
NO = ("no", "nope", "nah", "cancel", "stop", "never mind", "nevermind", "don't", "do not", "not now",
      "forget it", "scratch that", "wait")
FIND = ("find", "search", "look for", "looking for", "near", "nearby", "close by", "around here", "where can i",
        "where's a", "where is a", "i need", "i want", "i'd like", "get me", "show me", "any", "is there",
        "are there", "on the way", "on my way", "along", "craving", "hungry", "thirsty", "fill up", "low on")
CHECKOUT = ("check out", "checkout", "that's all", "that is all", "that's it", "that is it", "i'm done",
            "i am done", "done", "place the order", "place order", "place my order", "order it", "pay",
            "buy it", "complete the order", "finish the order", "ready to order", "submit")
CART = ("cart", "basket", "my order so far", "what's in my", "what is in my", "review my order")
MORE = ("more options", "other options", "something else", "show more", "anything else nearby", "what else",
        "next ones", "different place", "another place")
BACK = ("go back", "back", "previous", "return")
MENU_READ = ("what do they have", "what's on the menu", "what is on the menu", "read the menu", "menu",
             "what's good", "what do you recommend", "recommend", "popular", "what can i get", "options")
STATUS = ("where's my order", "where is my order", "order status", "status", "is it ready", "is my order ready",
          "how long", "when will it be ready", "track my order", "update on my order")
CANCEL_ORDER = ("cancel my order", "cancel the order", "cancel order")
DRIVE = ("take me there", "drive there", "navigate", "directions", "let's go", "lets go", "go there", "route",
         "head there", "drive to", "take me to", "get me there", "start navigation", "start the drive",
         "start driving", "go to it", "drive me")
STOP_NAV = ("stop navigation", "end navigation", "stop guidance", "cancel navigation", "stop the route",
            "end route", "cancel route", "stop navigating")
TABS = {"home": ("home screen", "go home screen", "open home", "show home", "home tab"),
        "agent": ("agent", "assistant screen", "open agent"),
        "shop": ("shop", "open shop", "show shop", "shop tab", "store tab"),
        "nav": ("map", "show the map", "open map", "open nav", "navigation screen", "show navigation", "nav")}
FACE = ("face id", "face", "scan my face", "use my face", "faceid", "face recognition", "look at the camera")
OTP = ("code", "otp", "one time", "one-time", "text me", "send me a code", "send a code", "sms", "pin",
       "passcode", "verification code", "send code")
VOICE_AUTH = ("voice", "by voice", "use my voice", "just confirm", "voice approval")
ALONG_ROUTE = re.compile(r"\b(?:on|along|near) (?:our|my|the|this|your) (?:route|way|trip|drive)\b|\bon route\b|"
                         r"\balong the way\b|\bon the way to\b")
ALONG_DEST = re.compile(r"\b(?:route|way|trip|drive|on route) (?:to|towards?) (.+)$")
NAVIGATE_TO = re.compile(r"\b(?:navigate|navigation|directions?|route|take me|drive me|drive|head|bring me|get me|"
                         r"guide me|lead me) (?:to|towards?)\s+(.+)$")
NAV_REFERENTIAL = re.compile(r"^(?:the |that |this |my )?(?:there|it|here|that|this|that place|the place|"
                             r"(?:first|second|third|fourth|fifth|last|closest|nearest|next)(?: one| option| place)?|"
                             r"one|number \d|order|pickup|pick up|merchant|restaurant|store|shop|stop|destination)$")
ADD_STOP = re.compile(r"\b(?:as a stop|as a waypoint|as a pit stop|to (?:my|the|our) (?:route|trip|stops)|"
                      r"(?:make|add) a stop|stop (?:at|by)|pull (?:in|over) (?:at|to))\b")
NAV_NOT_A_PLACE = re.compile(r"\b(?:cart|checkout|check out|menu|payment|screen|tab|settings|agent|shop tab|"
                             r"face id|code)\b")
LATE = re.compile(r"\b(?:running late|late by|be there in|i'll be there in|arrive in|here in|"
                  r"(\d+|\w+) minutes? (?:late|away|out))\b")


@dataclass
class Intent:
    name: str
    text: str
    slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    heard: list[str] = field(default_factory=list)


@dataclass
class Context:
    view: str = "idle"             # idle | places | menu | item | cart | checkout | order
    places: list[str] = field(default_factory=list)
    menu: Menu | None = None
    menu_category: str | None = None
    item: MenuItem | None = None
    cart_names: list[str] = field(default_factory=list)
    checkout_stage: str = ""       # choose | otp | face | voice | processing
    otp_length: int = 6
    voice_allowed: bool = True
    has_order: bool = False
    saved_places: dict[str, str] = field(default_factory=dict)  # alias -> place id
    place_tags: list[str] = field(default_factory=list)          # tags of the open merchant
    awaiting: str = ""             # "replace_cart" | "" — a yes/no the assistant is waiting for


def normalise(text: str) -> str:
    t = text.lower().strip().replace("’", "'").replace("‘", "'").replace("“", "").replace("”", "")
    t = re.sub(r"\b(hey|hi|ok|okay)\s+(nova|no va|noah|nava)\b", " ", t)
    t = re.sub(r"^\s*nova\b[,]?", " ", t)
    t = re.sub(r"[^\w%'$.\s-]", " ", t)
    t = re.sub(r"(?<!\d)\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def has(t: str, cues) -> str | None:
    for c in sorted(cues, key=len, reverse=True):
        if re.search(r"(?<![\w'])" + re.escape(c) + r"(?![\w'])", t):
            return c
    return None


def is_yes(t: str) -> bool:
    if re.search(r"\b(don't|do not|not|no|cancel)\b", t):
        return False
    return bool(has(t, YES))


APPROVE = re.compile(r"^(?:(?:yes|yeah|yep|yup)(?: (?:please|pay|confirm|approve|do it|go ahead))?|"
                     r"(?:i )?(?:approve|confirm)(?: (?:it|payment|the payment|the order|order|this))?|"
                     r"(?:please )?(?:pay|pay now|pay it|place (?:it|the order|order)|do it)(?: now| please)?|"
                     r"go ahead(?: and (?:pay|confirm|approve|place it))?|"
                     r"approved|confirmed)$")


def is_approval(t: str) -> bool:
    """Payment consent: a short utterance that is, in full, an explicit approval phrase."""
    if len(t.split()) > 6 or re.search(r"\b(don't|do not|not|no|cancel|later|never)\b", t):
        return False
    return bool(APPROVE.match(t))


def is_no(t: str) -> bool:
    return bool(has(t, NO)) and not has(t, ("no sugar", "no ice", "no onion", "no cheese", "no problem"))


# Words the recogniser writes for a digit that are also ordinary words: they count only next to a real digit.
AMBIGUOUS_DIGITS = frozenset({"oh", "o", "to", "too", "for", "fore", "ate", "won"})


def _digit_stream(t: str) -> list[tuple[str, str]]:
    """Tokens of a spoken code as (kind, digits): kind is 'd' (a digit, certain), 'a' (ambiguous word), 'r' (repeat)."""
    out: list[tuple[str, str]] = []
    for w in re.findall(r"[a-z]+|\d+", t.lower()):
        if w.isdigit():
            out.append(("d", w))
        elif w in ("double", "triple"):
            out.append(("r", "2" if w == "double" else "3"))
        elif w in AMBIGUOUS_DIGITS:
            out.append(("a", DIGIT_WORDS[w]))
        elif w in DIGIT_WORDS:
            out.append(("d", DIGIT_WORDS[w]))
        elif w in ("nought", "naught"):
            out.append(("d", "0"))
        else:
            out.append(("x", ""))
    return out


def parse_code(t: str, length: int | None = None) -> str:
    """The digits in a spoken one-time code: 'four seven two nine', '4729', "it's 4 7 2 9", 'oh' for 0,
    'double five'. Homophones ('to', 'for', 'oh') count only beside a digit, so filler words are not digits. When
    ``length`` is given and the count is one too many because the utterance starts with 'oh', that 'oh' is dropped."""
    toks = _digit_stream(t)
    live = [k in ("d", "r") for k, _ in toks]
    changed = True
    while changed:
        changed = False
        for i, (k, _) in enumerate(toks):
            if k == "a" and not live[i] and ((i > 0 and live[i - 1]) or (i + 1 < len(toks) and live[i + 1])):
                live[i], changed = True, True
    digits: list[str] = []
    first_ambiguous_oh = False
    i = 0
    while i < len(toks):
        k, v = toks[i]
        if k == "r":
            if i + 1 < len(toks) and live[i + 1] and toks[i + 1][0] != "r":
                digits.append(toks[i + 1][1][0] * int(v) + toks[i + 1][1][1:])
                i += 2
                continue
        elif live[i]:
            if not digits and k == "a" and v == "0":
                first_ambiguous_oh = True
            digits.append(v)
        i += 1
    code = "".join(digits)
    if length and len(code) == length + 1 and first_ambiguous_oh:
        code = code[1:]
    return code


def spoken_digits(t: str) -> str:
    """'four seven one nine two oh' / '471 920' / '4-7-1' -> '471920'."""
    return parse_code(t)


def quantity_before(t: str, start: int) -> int:
    pre = t[:start].split()[-3:]
    for w in reversed(pre):
        if w.isdigit():
            return max(1, min(20, int(w)))
        if w in NUM_WORDS and w not in ("to", "too", "for"):
            return NUM_WORDS[w]
        if w in ("to", "too", "for") and pre and pre[-1] == w:
            # "get to lattes" (STT for "two") only directly before the item
            return NUM_WORDS[w]
    return 1


MONEY = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:dollars?|bucks?|usd)\b|"
    r"\b(?:" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r"|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred)\s+(?:dollars?|bucks?)\b")


def strip_money(t: str) -> str:
    """Remove money expressions so their digits are not mistaken for a count."""
    return re.sub(r"\s+", " ", MONEY.sub(" ", t)).strip()


def is_prepay(item: MenuItem) -> bool:
    """Fuel / charging style items: the amount is an option, buying more than one makes no sense."""
    return any(g.id == "amount" for g in item.modifier_groups)


class IntentParser:
    def parse(self, text: str, ctx: Context) -> Intent:
        t = normalise(text)
        if not t:
            return Intent("none", text, confidence=0.0)

        # ---------- checkout: approvals and codes first, nothing else may be misheard as consent
        if ctx.view == "checkout":
            if ctx.checkout_stage == "otp":
                digits = parse_code(t, ctx.otp_length)
                if len(digits) == ctx.otp_length:
                    return Intent("otp_code", text, {"code": digits})
                if digits:      # too few or too many: ask again without spending one of the attempts
                    return Intent("otp_partial", text, {"heard": len(digits), "need": ctx.otp_length})
                if has(t, ("resend", "send again", "new code", "send another")):
                    return Intent("auth_method", text, {"method": "otp", "resend": True})
                if is_no(t):
                    return Intent("cancel_checkout", text)
                if is_approval(t) or t in ("ok", "okay", "sure", "yep"):
                    return Intent("otp_prompt", text)
            if has(t, FACE):
                return Intent("auth_method", text, {"method": "face"}, heard=[has(t, FACE)])
            if has(t, OTP):
                return Intent("auth_method", text, {"method": "otp"}, heard=[has(t, OTP)])
            if has(t, VOICE_AUTH):
                return Intent("auth_method", text, {"method": "voice"})
            if is_no(t):
                return Intent("cancel_checkout", text)
            if is_approval(t):
                return Intent("approve", text)

        if ctx.awaiting == "replace_cart":
            if is_yes(t) or has(t, ("new cart", "start over", "replace", "switch")):
                return Intent("replace_cart", text)
            if is_no(t):
                return Intent("keep_cart", text)

        if ctx.awaiting == "confirm_qty":
            if is_yes(t):
                return Intent("confirm_qty", text)
            if is_no(t):
                return Intent("cancel_qty", text)

        # ---------- global
        if has(t, CANCEL_ORDER):
            return Intent("cancel_order", text)
        m = LATE.search(t)
        if m and ctx.has_order:
            mins = re.search(r"(\d+)\s*min", t)
            n = int(mins.group(1)) if mins else next((v for k, v in NUM_WORDS.items()
                                                      if re.search(rf"\b{k}\b min", t)), 10)
            return Intent("update_eta", text, {"minutes": n})
        if ctx.has_order and has(t, ("i'm here", "i am here", "we're here", "we are here", "i've arrived",
                                     "i have arrived", "just arrived", "pulled up", "i'm at the pump")):
            return Intent("im_here", text)
        if has(t, STATUS) and (ctx.has_order or "order" in t):
            return Intent("status", text)
        if has(t, STOP_NAV):
            return Intent("stop_nav", text)
        if re.search(r"\b(use|switch to|turn on|enable|go to)\b.*\b(live |real |my )?(gps|location)\b", t) \
                and not re.search(r"\b(off|disable)\b", t):
            return Intent("vehicle_mode", text, {"mode": "gps"})
        if re.search(r"\b(simulat\w*|demo mode|turn off (the )?gps|disable (the )?gps)\b", t):
            return Intent("vehicle_mode", text, {"mode": "sim"})
        if t in ("help", "what can you do", "what can i say", "how does this work"):
            return Intent("help", text)
        if has(t, ("say that again", "repeat", "what did you say", "pardon")):
            return Intent("repeat", text)
        for tab, cues in TABS.items():
            if has(t, cues) and len(t.split()) <= 4 and not detect(t)[0]:
                return Intent("show_tab", text, {"tab": tab})
        place_hit = None
        for alias, pid in ctx.saved_places.items():
            if re.search(rf"\b(take me|drive|navigate|go|head|directions)\b.*\b{re.escape(alias)}\b", t):
                place_hit = pid
        if place_hit:
            return Intent("navigate_saved", text, {"place_id": place_hit})
        drive = has(t, DRIVE)

        # ---------- along the route / "navigate to X" (never inside a payment turn)
        if ctx.view != "checkout":
            along = self._along_route(t, text)
            if along:
                return along
            nav = self._navigate_to(t, text, ctx)
            if nav:
                return nav
            if ctx.places and ADD_STOP.search(t):
                return Intent("add_stop", text, {"index": self._select(t, ctx.places)})

        # ---------- item detail open
        if ctx.view == "item" and ctx.item is not None:
            opt = self._option(t, ctx.item)
            qty = self._qty_only(t, ctx.item)
            if has(t, ("add it", "add to cart", "add that", "add this", "i'll take it", "i'll have it", "yes",
                       "sure", "get it", "order it")) or (qty and has(t, ("add", "make it", "get"))):
                return Intent("add_item", text, {"item_id": ctx.item.id, "qty": qty or 1,
                                                 "selections": opt or {}})
            if opt:
                return Intent("set_option", text, {"selections": opt})
            if qty:
                return Intent("set_item_qty", text, {"qty": qty})
            if is_no(t) or has(t, BACK):
                return Intent("back", text)

        # ---------- cart edits / checkout
        if has(t, ("clear the cart", "clear cart", "empty the cart", "empty my cart", "start over")):
            return Intent("clear_cart", text)
        if ctx.cart_names:
            rem = re.search(r"\b(remove|take off|delete|drop|no more)\b(.*)", t)
            if rem:
                name = self._match_name(rem.group(2), ctx.cart_names)
                if name:
                    return Intent("remove_line", text, {"name": name})
            mk = re.search(r"\bmake (?:it|that|the (.+?)) (\w+)\b", t)
            if mk and (mk.group(2) in NUM_WORDS or mk.group(2).isdigit()):
                n = int(mk.group(2)) if mk.group(2).isdigit() else NUM_WORDS[mk.group(2)]
                name = self._match_name(mk.group(1) or "", ctx.cart_names) if mk.group(1) else None
                return Intent("set_line_qty", text, {"name": name, "qty": n})
            if has(t, CHECKOUT):
                return Intent("checkout", text)
        if has(t, CART) and len(t.split()) <= 7:
            return Intent("view_cart", text)

        # ---------- menu: ordering items by name
        if ctx.menu is not None and ctx.view in ("menu", "item", "cart"):
            items = self.extract_items(t, ctx.menu, ctx.menu_category)
            if items:
                verb = has(t, ("add", "order", "get", "i'll have", "i will have", "i want", "i'd like", "give me",
                               "can i get", "can i have", "and", "plus", "also", "i'll take", "make it"))
                counted = any(i["qty"] > 1 for i in items)
                return Intent("add_items" if verb or counted or len(items) > 1 else "open_item", text,
                              {"items": items}, heard=[i["heard"] for i in items])
            cat = self._menu_category(t, ctx.menu)
            if cat:
                return Intent("menu_category", text, {"category": cat}, heard=[cat])
            cat = self._menu_category(t, ctx.menu, need_verb=False)
            if cat:   # "some chips" with two kinds of chips: show them and ask
                return Intent("menu_category", text, {"category": cat, "ask": True}, heard=[cat])
            dcat, _ = detect(t)
            if has(t, MENU_READ) and not dcat:
                return Intent("read_menu", text)
            if dcat and dcat in ctx.place_tags and not has(t, ("find", "search", "another", "different", "else",
                                                                "nearby", "near", "other")):
                # "get me a coffee" while a coffee shop's menu is open: stay here
                return Intent("read_menu", text)

        # ---------- choosing among offered places
        if ctx.places and ctx.view in ("places", "idle", "menu", "cart"):
            idx = self._select(t, ctx.places)
            if idx is not None and (ctx.view == "places" or has(t, ("open", "pick", "choose", "go with",
                                                                      "select", "the ", "one"))):
                if drive:
                    return Intent("drive_to_place", text, {"index": idx})
                return Intent("open_place", text, {"index": idx}, heard=[ctx.places[idx]])
            if has(t, MORE) and ctx.view == "places":
                return Intent("more", text)

        if drive:
            return Intent("drive", text)
        if has(t, BACK) and len(t.split()) <= 3:
            return Intent("back", text)

        # ---------- discovery
        cat, kw = detect(t)
        if cat:
            query = self._query_for(t, kw)
            return Intent("find", text, {"category": cat, "query": query}, heard=[kw])
        if has(t, CHECKOUT) and ctx.cart_names:
            return Intent("checkout", text)
        if has(t, ("my usual", "the usual", "usual order", "same as last time")):
            return Intent("usual", text)
        if is_yes(t):
            return Intent("yes", text, confidence=0.7)
        if is_no(t):
            return Intent("no", text)
        return Intent("unknown", text, confidence=0.0)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _along_route(t: str, text: str) -> Intent | None:
        if not ALONG_ROUTE.search(t):
            return None
        cat, kw = detect(t)
        if not cat:
            return None
        slots: dict[str, Any] = {"category": cat, "query": IntentParser._query_for(t, kw)}
        m = ALONG_DEST.search(t)
        dest = m.group(1).strip() if m else ""
        if dest and not NAV_REFERENTIAL.match(dest):
            slots["destination"] = dest
        return Intent("find_along_route", text, slots, heard=[kw])

    @staticmethod
    def _navigate_to(t: str, text: str, ctx: Context) -> Intent | None:
        m = NAVIGATE_TO.search(t)
        if not m:
            return None
        target = re.sub(r"\s+(?:please|now|thanks|thank you)$", "", m.group(1).strip())
        if len(target) < 3 or NAV_REFERENTIAL.match(target) or NAV_NOT_A_PLACE.search(target):
            return None
        if ctx.places and IntentParser._select(target, ctx.places) is not None:
            return None
        cat, kw = detect(target)
        if cat and (kw == target or re.match(r"^(?:a|an|any|some|the|nearest|closest)\b", target)):
            return None            # "take me to a coffee shop" is a search, not a place name
        return Intent("navigate_to", text, {"query": target})

    @staticmethod
    def _query_for(t: str, kw: str) -> str | None:
        """A specific thing inside a category request, e.g. 'latte' in 'find me a latte' -> 'latte'."""
        generic = {"food", "eat", "hungry", "coffee", "gas", "fuel", "store", "bar", "bars", "charging",
                   "charger", "pharmacy", "pharmacies", "restaurant", "meal", "lunch", "dinner", "breakfast", "snack",
                   "sandwich", "sandwiches", "drinks", "groceries", "grocery", "market", "brunch", "cafe",
                   "gas station", "shop", "fill up"}
        return None if kw in generic else kw

    @staticmethod
    def _qty_only(t: str, item: MenuItem | None = None) -> int | None:
        """A bare count. Money ("$80", "80 dollars", "ten bucks") and numbers that name an option are never one;
        a prepay item is always a single purchase."""
        if item is not None and is_prepay(item):
            return None
        t = strip_money(t)
        if item is not None:
            for g in item.modifier_groups:
                for o in g.options:
                    for n in (o.name.lower(), *o.aliases):
                        t = re.sub(r"(?<![\w$])" + re.escape(n.lstrip("$")) + r"(?![\w])", " ", t) \
                            if n.lstrip("$").isdigit() else t
        m = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b(?! (?:minutes?|min\b))", t)
        if not m:
            return None
        w = m.group(1)
        return int(w) if w.isdigit() else NUM_WORDS.get(w)

    @staticmethod
    def _alias_rx(n: str) -> re.Pattern[str]:
        """An option word as a whole word, tolerating a plural ("two diet cokes", "red bulls")."""
        plural = "" if n.isdigit() or not n[-1:].isalpha() else r"(?:e?s)?"
        return re.compile(r"(?<![\w])" + re.escape(n) + plural + r"(?![\w])")

    @classmethod
    def _option(cls, t: str, item: MenuItem) -> dict[str, list[str]] | None:
        out: dict[str, list[str]] = {}
        for g in item.modifier_groups:
            best = None
            many: list[str] = []
            for o in g.options:
                names = [o.name.lower(), *o.aliases]
                if o.name.startswith("$") and o.name[1:].isdigit():
                    names.append(o.name[1:])
                for n in names:
                    if n and cls._alias_rx(n).search(t):
                        if best is None or len(n) > best[1]:
                            best = (o.id, len(n))
                        if o.id not in many:
                            many.append(o.id)
                        break
            if g.max_select > 1 and many:
                out[g.id] = many[: g.max_select]
            elif best:
                out[g.id] = [best[0]]
        return out or None

    @classmethod
    def _brand_item(cls, core: str, menu: Menu, used: set[str]) -> tuple[str, float, str] | None:
        """"two Diet Cokes", "a Red Bull": a brand option names the item that carries the brand group."""
        best: tuple[int, str, str] | None = None
        for it in menu.items:
            g = it.group("brand")
            if not g or it.id in used or not it.available:
                continue
            for o in g.options:
                for n in (o.name.lower(), *o.aliases):
                    if n and len(n) >= 3 and cls._alias_rx(n).search(core) and (best is None or len(n) > best[0]):
                        best = (len(n), it.id, it.name.lower())
        return (best[1], 96.0, best[2]) if best else None

    @staticmethod
    def _match_name(fragment: str, names: list[str]) -> str | None:
        frag = fragment.strip()
        if not frag:
            return None
        res = process.extractOne(frag, names, scorer=fuzz.token_set_ratio, processor=utils.default_process)
        return res[0] if res and res[1] >= 70 else None

    @staticmethod
    def _select(t: str, options: list[str]) -> int | None:
        for w, i in ORDINALS.items():
            if re.search(rf"\b(the )?{w}( one| option| place| stop)?\b", t) and (
                    w not in ("one", "two", "three", "four", "five", "six") or re.search(
                        rf"\b(number|option|the) {w}\b", t)):
                return (len(options) - 1) if i < 0 else (i if i < len(options) else None)
        m = re.search(r"\bnumber (\d)\b", t)
        if m and 0 < int(m.group(1)) <= len(options):
            return int(m.group(1)) - 1
        names = [re.sub(r"\(.*?\)", "", o).lower().strip() for o in options]
        res = process.extractOne(t, names, scorer=fuzz.partial_token_set_ratio)
        if res and res[1] >= 86:
            # require a distinctive word of the name, not just "coffee"
            name_words = [w for w in re.findall(r"[a-z']+", res[0]) if len(w) > 3 and w not in (
                "coffee", "cafe", "restaurant", "kitchen", "store", "market", "station", "deli", "the")]
            if any(re.search(rf"\b{re.escape(w)}\b", t) for w in name_words) or len(name_words) == 0:
                return res[2]
        return None

    @staticmethod
    def _menu_category(t: str, menu: Menu, *, need_verb: bool = True) -> str | None:
        if need_verb and not re.search(r"\b(show|open|see|what|any|look|browse|go to|what's|whats|list)\b", t):
            return None
        words = {w.rstrip("s") for w in re.findall(r"[a-z]+", t) if len(w) > 3}
        best = None
        for c in menu.categories:
            toks = [w.rstrip("s") for w in re.findall(r"[a-z]+", c.lower()) if len(w) > 3]
            hit = sum(1 for w in toks if w in words)
            if hit and (best is None or hit > best[1]):
                best = (c, hit)
        return best[0] if best else None

    _STOP = {"the", "and", "with", "some", "one", "please", "large", "small", "medium", "regular", "order", "get",
             "want", "have", "like", "from", "them", "this", "that", "your", "their"}

    @classmethod
    def _keyword_item(cls, core: str, menu: Menu, used: set[str]) -> tuple[str, float, str] | None:
        """Fallback when no name matches closely: shared words, preferring the head noun
        ("a chocolate chip cookie" -> "Signature Cookies")."""
        def stems(x: str) -> list[str]:
            return [w[:-1] if w.endswith("s") and len(w) > 4 else w
                    for w in re.findall(r"[a-z]+", x.lower()) if len(w) > 3 and w not in cls._STOP]
        words = stems(core)
        if not words:
            return None
        head = words[-1]
        scored = []
        for it in menu.items:
            if it.id in used or not it.available:
                continue
            name_words = set(stems(it.name)) | set(stems(it.category or ""))
            overlap = sum(1 for w in words if w in name_words)
            if overlap:
                scored.append((overlap + (0.5 if head in name_words else 0), it.id, it.name.lower()))
        if not scored:
            return None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None  # ambiguous: let the user say which
        return (scored[0][1], 80.0, scored[0][2])

    def extract_items(self, t: str, menu: Menu, category: str | None = None) -> list[dict[str, Any]]:
        """Item mentions in an utterance, with quantity and option words. Longest, best matches win."""
        names = {i.id: i.name.lower() for i in menu.items if i.available}
        # split on conjunctions so "a turkey pesto and two cold brews" yields two mentions
        parts = re.split(r"\b(?:and|plus|also|with a|,)\b", t)
        found: list[dict[str, Any]] = []
        used: set[str] = set()
        for part in parts:
            part = part.strip()
            core = re.sub(r"^(?:can i (?:get|have)|i'd like|i would like|i'll have|i will have|i want|"
                          r"give me|get me|add|order|get|please|let me get|and)\s+", "", part)
            core = re.sub(r"\b(to the cart|to my cart|to cart|please|from here|from them|for me)\b", "", core).strip()
            if len(core) < 3:
                continue
            words = core.split()
            # drop leading quantity words for matching
            qty = 1
            if words and (words[0].isdigit() or words[0] in NUM_WORDS) and not MONEY.match(core):
                qty = int(words[0]) if words[0].isdigit() else NUM_WORDS[words[0]]
                core = " ".join(words[1:])
            money = MONEY.match(core)
            if money:
                core = core[money.end():].strip()
                core = re.sub(r"^(?:of|worth of|worth)\s+", "", core)
            if not core:
                continue
            cands = process.extract(core, names, scorer=fuzz.token_set_ratio, limit=5)
            best = self._brand_item(core, menu, used)
            for name, score, iid in ([] if best else cands):
                if iid in used:
                    continue
                item = menu.item(iid)
                bonus = 3 if category and item and item.category == category else 0
                # whole-name containment is strong evidence ("cold brew" in "two cold brews")
                if name in core or core.rstrip("s") in name:
                    bonus += 8
                if score + bonus >= 82 and (best is None or (score + bonus, len(name)) > (best[1], len(best[2]))):
                    best = (iid, score + bonus, name)
            if not best:
                best = self._keyword_item(core, menu, used)
            if best:
                item = menu.item(best[0])
                used.add(best[0])
                # the item's own name is not an option word ("bacon burger" is not "add bacon")
                sel = self._option(re.sub(r"(?<![\w])" + re.escape(item.name.lower()) + r"s?(?![\w])", " ", part), item) or {}
                found.append({"item_id": best[0], "qty": 1 if is_prepay(item) else max(1, min(20, qty)),
                              "heard": core, "selections": sel})
        return found
