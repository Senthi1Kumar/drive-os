"""Human formatting for the HMI and for speech (units, clock, durations)."""

from __future__ import annotations

from datetime import datetime


def clock(dt: datetime | None = None) -> str:
    dt = (dt or datetime.now().astimezone()).astimezone()
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}"


def greeting(dt: datetime | None = None) -> str:
    h = (dt or datetime.now().astimezone()).astimezone().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


def distance(m: float, units: str = "imperial") -> tuple[str, str]:
    """Maneuver distance → (value, unit)."""
    if units == "metric":
        if m < 950:
            return f"{int(round(m / 10.0) * 10)}", "m"
        return f"{m / 1000:.1f}", "km"
    feet = m * 3.28084
    if feet < 700:
        return f"{max(50, int(round(feet / 50.0) * 50))}", "ft"
    return f"{m / 1609.344:.1f}", "mi"


def trip_distance(m: float, units: str = "imperial") -> str:
    if units == "metric":
        return f"{m / 1000:.1f} km"
    return f"{m / 1609.344:.1f} mi"


def minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


def duration(seconds: float) -> str:
    m = minutes(seconds)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def spoken_minutes(n: int) -> str:
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen"]
    w = words[n] if 0 <= n < len(words) else str(n)
    return f"{w} minute" + ("" if n == 1 else "s")


def speed(mps: float, units: str = "imperial") -> str:
    return f"{round(mps * 3.6)} km/h" if units == "metric" else f"{round(mps * 2.23694)} mph"


def temp(f: float | None, units: str = "imperial") -> str:
    if f is None:
        return ""
    return f"{round((f - 32) * 5 / 9)}°C" if units == "metric" else f"{round(f)}°F"


def ordinal_word(i: int) -> str:
    return ["first", "second", "third", "fourth", "fifth"][i] if i < 5 else f"number {i + 1}"
