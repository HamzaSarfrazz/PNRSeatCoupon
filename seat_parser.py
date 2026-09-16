"""
seat_parser.py
--------------
Reads a PNR Admin page (copy/paste or .txt) and pulls out:

  * the reservation date/time and the payment date/time, straight from the page
  * every seat assignment / seat change entry in the Reservation History

and builds the report:

    S No | Coupon Number | Initial Assignment Date | Initial Seat |
    Seat Change Date | New Seat | Status

The parser does NOT depend on tabs, columns or line breaks. It flattens the page,
splits it at every date+time stamp, and reads each resulting chunk. That way it works
whether the page was pasted from the browser or saved as a text file first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# --------------------------------------------------------------------------- #
# Date / time handling
# --------------------------------------------------------------------------- #
D = r"\d{1,2}-[A-Za-z]{3,9}-\d{4}"                    # 30-Jun-2026
T = r"\d{1,2}:\d{2}(?::\d{2})?\s*[AaPp]\.?[Mm]\.?"    # 11:17 PM / 11:17PM

# date and time sitting on two separate lines -> glue them together
GLUE_RE = re.compile(r"(" + D + r")[ \t]*[\r\n]+[ \t]*(" + T + r")")
STAMP_RE = re.compile(r"(" + D + r")\s+(" + T + r")")


def parse_dt(date_str: str, time_str: str) -> datetime | None:
    raw = f"{date_str.strip()} {time_str.strip().upper().replace('.', '').replace(' ', '')}"
    for fmt in ("%d-%b-%Y %I:%M%p", "%d-%B-%Y %I:%M%p",
                "%d-%b-%Y %I:%M:%S%p", "%d-%B-%Y %I:%M:%S%p"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def flatten(text: str) -> str:
    """Glue split date/time stamps and normalise whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = GLUE_RE.sub(r"\1 \2", text)
    return text.replace("\t", " ").replace("\n", " \n ")


# --------------------------------------------------------------------------- #
# Seat event patterns
# --------------------------------------------------------------------------- #
SEAT = r"(\d{1,2}\s?[A-Fa-f])"
CPN = r"(\d{6,})"

PATTERNS = [
    # "Change seat from 23C to 23F on coupon 86901606"
    ("CHANGE", re.compile(
        r"Change\s+seat\s+from\s+" + SEAT + r"\s+to\s+" + SEAT +
        r"\s+on\s+coupon\s*#?\s*" + CPN, re.I), ("old", "new", "coupon")),

    # "Seat reassignment for coupon 86901605: 25C to 28D"
    ("CHANGE", re.compile(
        r"Seat\s+reassignment\s+for\s+coupon\s*#?\s*" + CPN + r"\s*:?\s*" +
        SEAT + r"\s+to\s+" + SEAT, re.I), ("coupon", "old", "new")),

    # "Change Seat for Coupon 86901549: 37E to 29B"   and   "...: to 29C"
    ("CHANGE", re.compile(
        r"Change\s+Seat\s+for\s+Coupon\s*#?\s*" + CPN + r"\s*:?\s*(?:" +
        SEAT + r"\s+)?to\s+" + SEAT, re.I), ("coupon", "old", "new")),

    # "Assign seat 30F to coupon 86901522"
    ("ASSIGN", re.compile(
        r"Assign\s+seat\s+" + SEAT + r"\s+to\s+coupon\s*#?\s*" + CPN, re.I),
     ("seat", "coupon")),

    # "Coupon: 86901605 seat:28D is assigned."
    ("ASSIGN", re.compile(
        r"Coupon\s*:?\s*#?\s*" + CPN + r"\s*seat\s*:?\s*" + SEAT +
        r"\s*is\s+assigned", re.I), ("coupon", "seat")),
]

# rows that mention a coupon and a seat but are not seat movements
NOISE_RE = re.compile(r"SSR\s+(Added|Removed)|SEAT\s+(added|removed)\s+(to|from)\s+coupon",
                      re.I)


@dataclass
class SeatEvent:
    when: datetime
    coupon: str
    kind: str                 # ASSIGN | CHANGE
    old_seat: str | None
    new_seat: str
    user: str
    details: str


def _norm_seat(s: str | None) -> str | None:
    return s.replace(" ", "").upper() if s else None


def _history_text(text: str, whole_document: bool = False) -> str:
    """Everything after the 'Reservation History' heading (or the whole page)."""
    if whole_document:
        return text
    last = None
    for last in re.finditer(r"Reservation\s+History", text, re.I):
        pass
    return text[last.end():] if last else text


def _chunks(flat: str):
    """Split flattened text at every date+time stamp -> (datetime, chunk_text)."""
    stamps = list(STAMP_RE.finditer(flat))
    for i, m in enumerate(stamps):
        dt = parse_dt(m.group(1), m.group(2))
        if dt is None:
            continue
        end = stamps[i + 1].start() if i + 1 < len(stamps) else len(flat)
        yield dt, flat[m.end():end]


_STOP_WORDS = {"assign", "change", "seat", "seats", "pax", "data", "ticket",
               "coupon", "coupons", "ssr", "ssrs", "add", "added", "remove",
               "removed", "expiration", "time", "info", "reassignment"}


def _user_of(chunk: str) -> str:
    m = re.search(
        r"\b(?:Agent|PA|System)\s+([A-Z][A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,4})",
        chunk,
    )
    if not m:
        return ""
    words = m.group(1).split()
    while words and words[-1].lower() in _STOP_WORDS:
        words.pop()
    return " ".join(words)


def extract_events(text: str, whole_document: bool = False) -> list[SeatEvent]:
    """Every seat assignment / change found on the page, chronological."""
    flat = flatten(_history_text(text, whole_document))
    events: list[SeatEvent] = []

    for dt, chunk in _chunks(flat):
        user = _user_of(chunk)
        for kind, rx, names in PATTERNS:
            for m in rx.finditer(chunk):
                vals = dict(zip(names, m.groups()))
                coupon = vals.get("coupon")
                if not coupon:
                    continue
                events.append(SeatEvent(
                    when=dt,
                    coupon=coupon,
                    kind=kind,
                    old_seat=_norm_seat(vals.get("old")),
                    new_seat=_norm_seat(vals.get("new") or vals.get("seat")) or "",
                    user=user,
                    details=" ".join(m.group(0).split()),
                ))

    events.sort(key=lambda e: (e.when, e.coupon, e.kind))
    return _dedupe(events)


def _dedupe(events: list[SeatEvent]) -> list[SeatEvent]:
    """
    The website logs one movement twice at the same timestamp:
        "Seat reassignment for coupon X: 25C to 28D"  (CHANGE)
        "Coupon: X seat:28D is assigned."             (ASSIGN)
    Keep the CHANGE, and drop identical repeats.
    """
    change_keys = {(e.coupon, e.when, e.new_seat) for e in events if e.kind == "CHANGE"}
    seen, out = set(), []
    for e in events:
        if e.kind == "ASSIGN" and (e.coupon, e.when, e.new_seat) in change_keys:
            continue
        key = (e.coupon, e.when, e.kind, e.old_seat, e.new_seat)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# --------------------------------------------------------------------------- #
# Reservation date / payment date detection
# --------------------------------------------------------------------------- #
def detect_dates(text: str) -> dict[str, datetime | None]:
    """
    Reservation -> 'Ticket Time <stamp>', else the Installment Payment row,
                   else the earliest seat entry.
    Payment     -> the 'Ticket Sale' row of the Travel Agency / Payment Center log.
    """
    flat = flatten(text)

    def stamp_before(keyword_rx: str, look_back: int = 500) -> datetime | None:
        """Datetime of the record that contains `keyword_rx`."""
        best = None
        for m in re.finditer(keyword_rx, flat, re.I):
            window = flat[max(0, m.start() - look_back): m.start()]
            stamps = list(STAMP_RE.finditer(window))
            if not stamps:
                continue
            dt = parse_dt(stamps[-1].group(1), stamps[-1].group(2))
            if dt and (best is None or dt > best):
                best = dt
        return best

    reservation = None
    m = re.search(r"Ticket\s+Time\s+(" + D + r")\s+(" + T + r")", flat, re.I)
    if m:
        reservation = parse_dt(m.group(1), m.group(2))
    if reservation is None:
        reservation = stamp_before(r"Installment\s+Payment")
    if reservation is None:
        evs = extract_events(text)
        reservation = evs[0].when if evs else None

    payment = stamp_before(r"Ticket\s+Sale")

    return {"reservation": reservation, "payment": payment}


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
DATE_FMT = "%d-%b-%y"
DATETIME_FMT = "%d-%b-%Y %I:%M %p"

SUMMARY_COLS = ["S No", "Coupon Number", "Initial Assignment Date", "Initial Seat",
                "Seat Change Date", "New Seat", "Status"]


def filter_events(events, start: datetime, end: datetime) -> list[SeatEvent]:
    return [e for e in events if start <= e.when <= end]


def build_summary(events: list[SeatEvent], date_fmt: str = DATE_FMT) -> pd.DataFrame:
    """One row per coupon: first seat inside the window vs. last seat inside it."""
    by_coupon: dict[str, list[SeatEvent]] = {}
    for e in events:
        by_coupon.setdefault(e.coupon, []).append(e)

    rows = []
    for coupon, evs in by_coupon.items():
        evs.sort(key=lambda x: x.when)
        first = evs[0]
        initial_seat = first.old_seat or first.new_seat
        initial_when = first.when

        final_seat, final_when = initial_seat, initial_when
        for e in evs:
            seat = e.new_seat or e.old_seat
            if seat and seat != final_seat:
                final_seat, final_when = seat, e.when

        changed = final_seat != initial_seat
        rows.append({
            "Coupon Number": coupon,
            "Initial Assignment Date": initial_when.strftime(date_fmt),
            "Initial Seat": initial_seat,
            "Seat Change Date": final_when.strftime(date_fmt) if changed else "",
            "New Seat": final_seat if changed else "",
            "Status": "Changed" if changed else "Unchanged",
            "_first": initial_when,
        })

    if not rows:
        return pd.DataFrame(columns=SUMMARY_COLS)

    df = pd.DataFrame(rows).sort_values(["_first", "Coupon Number"],
                                        ascending=[True, False])
    df = df.drop(columns=["_first"]).reset_index(drop=True)
    df.insert(0, "S No", range(1, len(df) + 1))
    return df[SUMMARY_COLS]


def build_event_log(events: list[SeatEvent]) -> pd.DataFrame:
    rows = [{
        "Date / Time": e.when.strftime(DATETIME_FMT),
        "Coupon Number": e.coupon,
        "Event": "Seat Change" if e.kind == "CHANGE" else "Seat Assigned",
        "From Seat": e.old_seat or "",
        "To Seat": e.new_seat,
        "User": e.user,
        "Log Text": e.details,
    } for e in sorted(events, key=lambda x: (x.when, x.coupon))]

    df = pd.DataFrame(rows, columns=["Date / Time", "Coupon Number", "Event",
                                     "From Seat", "To Seat", "User", "Log Text"])
    if not df.empty:
        df.insert(0, "S No", range(1, len(df) + 1))
    return df


def unmatched_lines(text: str, whole_document: bool = False) -> list[str]:
    """Lines mentioning a coupon and a seat that produced no event - for debugging."""
    body = _history_text(text, whole_document)
    out = []
    for ln in body.splitlines():
        if not (re.search(r"coupon", ln, re.I) and re.search(r"seat", ln, re.I)):
            continue
        if NOISE_RE.search(ln) or any(rx.search(ln) for _, rx, _ in PATTERNS):
            continue
        out.append(" ".join(ln.split()))
    return out[:50]
