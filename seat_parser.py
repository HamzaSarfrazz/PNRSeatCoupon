"""
seat_parser.py
--------------
Reads a PNR Admin page (copy/paste or .txt) and pulls out:

  * the reservation date/time and the payment date/time, straight from the page
  * every seat assignment / seat change entry in the Reservation History, tagged
    with who did it and what kind of actor they are
  * cases where the SAME actor removed a coupon's seat and then reassigned it a
    different seat shortly after - a pattern worth a human's attention, since it
    can be used to move a passenger to a cheaper seat right before an aircraft
    change so a costlier one can be requested back later as compensation

and builds the report:

    S No | Coupon Number | Initial Assignment Date | Initial Seat |
    Seat Change Date | New Seat | Status

The parser does NOT depend on tabs, columns or line breaks. It flattens the page,
splits it at every date+time stamp, and reads each resulting chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

__version__ = "2026-09-17.2-flags"

# --------------------------------------------------------------------------- #
# Date / time handling
# --------------------------------------------------------------------------- #
D = r"\d{1,2}-[A-Za-z]{3,9}-\d{4}"                    # 30-Jun-2026
T = r"\d{1,2}:\d{2}(?::\d{2})?\s*[AaPp]\.?[Mm]\.?"    # 11:17 PM / 11:17PM

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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = GLUE_RE.sub(r"\1 \2", text)
    return text.replace("\t", " ").replace("\n", " \n ")


# --------------------------------------------------------------------------- #
# Actor (who did it) classification
# --------------------------------------------------------------------------- #
ACTION_PHRASES = [
    "Change Pax Data", "Change Coupon SSRs", "Assign Seat", "Pax Info Changed",
    "Pax Data Changed", "Change Expiration Time", "Change Coupon Status",
    "Void Coupon", "Reissue", "Cancel Coupon", "Change Seat",
]
_ACTION_ALT = "|".join(re.escape(a) for a in ACTION_PHRASES)
ACTOR_RE = re.compile(
    r"^\s*\d+\s+(PA|Agent|System)\s+(.+?)\s+(?:" + _ACTION_ALT + r")\b", re.I
)

WEBSITE_NAMES = {"airblue website", "airblue app", "website", "web check-in",
                  "self service", "self-service", "online check-in"}


def classify_role(user_type: str | None, user_name: str | None) -> str:
    """PA -> Employee. Agent named e.g. 'Airblue Website' -> System / Self-Service.
    Any other Agent -> Travel Agent."""
    if not user_type:
        return "Unknown"
    ut = user_type.strip().lower()
    name = (user_name or "").strip()
    if ut == "pa":
        return "Employee"
    if ut == "agent":
        if name.lower() in WEBSITE_NAMES or "website" in name.lower() or "app" in name.lower():
            return "System / Self-Service"
        return "Travel Agent"
    if ut == "system":
        return "System"
    return user_type.strip().title()


def actor_of(chunk: str) -> tuple[str, str, str]:
    """Return (user_type, user_name, role) for a record chunk."""
    m = ACTOR_RE.match(chunk)
    if m:
        user_type, name = m.group(1), m.group(2).strip()
        return user_type, name, classify_role(user_type, name)
    # fallback: best-effort name only, role unknown
    m2 = re.search(r"\b(?:Agent|PA|System)\s+([A-Z][A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,4})", chunk)
    name = m2.group(1).strip() if m2 else ""
    return "", name, "Unknown"


# --------------------------------------------------------------------------- #
# Seat event patterns
# --------------------------------------------------------------------------- #
SEAT = r"(\d{1,2}\s?[A-Fa-f])"
CPN = r"(\d{6,})"

PATTERNS = [
    ("CHANGE", re.compile(
        r"Change\s+seat\s+from\s+" + SEAT + r"\s+to\s+" + SEAT +
        r"\s+on\s+coupon\s*#?\s*" + CPN, re.I), ("old", "new", "coupon")),

    ("CHANGE", re.compile(
        r"Seat\s+reassignment\s+for\s+coupon\s*#?\s*" + CPN + r"\s*:?\s*" +
        SEAT + r"\s+to\s+" + SEAT, re.I), ("coupon", "old", "new")),

    ("CHANGE", re.compile(
        r"Change\s+Seat\s+for\s+Coupon\s*#?\s*" + CPN + r"\s*:?\s*(?:" +
        SEAT + r"\s+)?to\s+" + SEAT, re.I), ("coupon", "old", "new")),

    ("ASSIGN", re.compile(
        r"Assign\s+seat\s+" + SEAT + r"\s+to\s+coupon\s*#?\s*" + CPN, re.I),
     ("seat", "coupon")),

    ("ASSIGN", re.compile(
        r"Coupon\s*:?\s*#?\s*" + CPN + r"\s*seat\s*:?\s*" + SEAT +
        r"\s*is\s+assigned", re.I), ("coupon", "seat")),
]

REMOVE_RE = re.compile(r"SEAT\s+removed\s+from\s+coupon\s+ID#\s*" + CPN, re.I)
ADD_SSR_RE = re.compile(r"SEAT\s+added\s+to\s+coupon\s+ID#\s*" + CPN, re.I)


@dataclass
class SeatEvent:
    when: datetime
    idx: int                  # position in the page, top (newest) = 0
    coupon: str
    kind: str                 # ASSIGN | CHANGE
    old_seat: str | None
    new_seat: str
    user: str
    role: str
    details: str


@dataclass
class SsrEvent:
    when: datetime
    idx: int
    coupon: str
    kind: str                 # REMOVE | ADD
    user: str
    role: str


def _norm_seat(s: str | None) -> str | None:
    return s.replace(" ", "").upper() if s else None


def _history_text(text: str, whole_document: bool = False) -> str:
    if whole_document:
        return text
    last = None
    for last in re.finditer(r"Reservation\s+History", text, re.I):
        pass
    return text[last.end():] if last else text


def _chunks(flat: str):
    """Split flattened text at every date+time stamp -> (idx, datetime, chunk_text).
    idx counts from 0 at the top of the text (i.e. the newest record in a page
    that lists history newest-first)."""
    stamps = list(STAMP_RE.finditer(flat))
    for i, m in enumerate(stamps):
        dt = parse_dt(m.group(1), m.group(2))
        if dt is None:
            continue
        end = stamps[i + 1].start() if i + 1 < len(stamps) else len(flat)
        yield i, dt, flat[m.end():end]


def _sort_key(when: datetime, idx: int):
    """Chronological order (oldest first). Page is newest-first overall, and
    within one displayed minute the first-listed record is the most recent, so
    a larger idx (further down the page) among equal timestamps is earlier."""
    return (when, -idx)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_events(text: str, whole_document: bool = False) -> list[SeatEvent]:
    """Every seat assignment / change found on the page, chronological."""
    flat = flatten(_history_text(text, whole_document))
    events: list[SeatEvent] = []

    for idx, dt, chunk in _chunks(flat):
        _, user, role = actor_of(chunk)
        for kind, rx, names in PATTERNS:
            for m in rx.finditer(chunk):
                vals = dict(zip(names, m.groups()))
                coupon = vals.get("coupon")
                if not coupon:
                    continue
                events.append(SeatEvent(
                    when=dt,
                    idx=idx,
                    coupon=coupon,
                    kind=kind,
                    old_seat=_norm_seat(vals.get("old")),
                    new_seat=_norm_seat(vals.get("new") or vals.get("seat")) or "",
                    user=user,
                    role=role,
                    details=" ".join(m.group(0).split()),
                ))

    events.sort(key=lambda e: _sort_key(e.when, e.idx))
    return _dedupe(events)


def extract_ssr_events(text: str, whole_document: bool = False) -> list[SsrEvent]:
    """Every 'SEAT removed/added to coupon ID# ...' entry, with its actor."""
    flat = flatten(_history_text(text, whole_document))
    out: list[SsrEvent] = []
    for idx, dt, chunk in _chunks(flat):
        _, user, role = actor_of(chunk)
        m = REMOVE_RE.search(chunk)
        if m:
            out.append(SsrEvent(dt, idx, m.group(1), "REMOVE", user, role))
            continue
        m = ADD_SSR_RE.search(chunk)
        if m:
            out.append(SsrEvent(dt, idx, m.group(1), "ADD", user, role))
    out.sort(key=lambda e: _sort_key(e.when, e.idx))
    return out


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
        key = (e.coupon, e.when, e.kind, e.old_seat, e.new_seat, e.idx)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# --------------------------------------------------------------------------- #
# Self removal + reassignment ("suspicious") detection
# --------------------------------------------------------------------------- #
FLAGGABLE_ROLES = {"Employee", "Travel Agent"}


def find_self_reassignments(text: str, whole_document: bool = False) -> list[dict]:
    """
    Flags coupons where the SAME person removed the seat (SEAT removed from
    coupon ID# ...) and then assigned a new seat to that same coupon
    afterwards. Website / self-service actors are excluded - this looks for
    staff or travel-agent side actions only.
    """
    seat_events = extract_events(text, whole_document)
    ssr_events = extract_ssr_events(text, whole_document)

    by_coupon_seats: dict[str, list[SeatEvent]] = {}
    for e in seat_events:
        by_coupon_seats.setdefault(e.coupon, []).append(e)
    for lst in by_coupon_seats.values():
        lst.sort(key=lambda x: _sort_key(x.when, x.idx))

    by_coupon_removes: dict[str, list[SsrEvent]] = {}
    for s in ssr_events:
        if s.kind == "REMOVE":
            by_coupon_removes.setdefault(s.coupon, []).append(s)

    flags: list[dict] = []
    for coupon, removes in by_coupon_removes.items():
        seats = by_coupon_seats.get(coupon, [])
        if not seats:
            continue
        for rem in removes:
            if rem.role not in FLAGGABLE_ROLES:
                continue
            rem_key = _sort_key(rem.when, rem.idx)
            after = [s for s in seats
                     if s.kind == "ASSIGN" and s.user == rem.user
                     and _sort_key(s.when, s.idx) >= rem_key]
            if not after:
                continue
            after.sort(key=lambda x: _sort_key(x.when, x.idx))
            nxt = after[0]
            before = [s for s in seats if _sort_key(s.when, s.idx) < rem_key]
            prev_seat = before[-1].new_seat if before else None
            flags.append({
                "Coupon Number": coupon,
                "User": rem.user,
                "Role": rem.role,
                "Seat Removed At": rem.when,
                "Reassigned At": nxt.when,
                "Previous Seat": prev_seat or "(unknown)",
                "New Seat": nxt.new_seat,
            })
    flags.sort(key=lambda f: f["Seat Removed At"])
    return flags


# --------------------------------------------------------------------------- #
# Reservation date / payment date detection
# --------------------------------------------------------------------------- #
def detect_dates(text: str) -> dict[str, datetime | None]:
    flat = flatten(text)

    def stamp_before(keyword_rx: str, look_back: int = 500) -> datetime | None:
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
    by_coupon: dict[str, list[SeatEvent]] = {}
    for e in events:
        by_coupon.setdefault(e.coupon, []).append(e)

    rows = []
    for coupon, evs in by_coupon.items():
        evs.sort(key=lambda x: _sort_key(x.when, x.idx))
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
        "Role": e.role,
        "Log Text": e.details,
    } for e in sorted(events, key=lambda x: _sort_key(x.when, x.idx))]

    df = pd.DataFrame(rows, columns=["Date / Time", "Coupon Number", "Event",
                                     "From Seat", "To Seat", "User", "Role", "Log Text"])
    if not df.empty:
        df.insert(0, "S No", range(1, len(df) + 1))
    return df


def build_flags_table(flags: list[dict], date_fmt: str = DATETIME_FMT) -> pd.DataFrame:
    cols = ["S No", "Coupon Number", "User", "Role", "Seat Removed At",
            "Reassigned At", "Previous Seat", "New Seat"]
    if not flags:
        return pd.DataFrame(columns=cols)
    rows = []
    for f in flags:
        rows.append({
            "Coupon Number": f["Coupon Number"],
            "User": f["User"],
            "Role": f["Role"],
            "Seat Removed At": f["Seat Removed At"].strftime(date_fmt),
            "Reassigned At": f["Reassigned At"].strftime(date_fmt),
            "Previous Seat": f["Previous Seat"],
            "New Seat": f["New Seat"],
        })
    df = pd.DataFrame(rows)
    df.insert(0, "S No", range(1, len(df) + 1))
    return df[cols]


def unmatched_lines(text: str, whole_document: bool = False) -> list[str]:
    body = _history_text(text, whole_document)
    out = []
    for ln in body.splitlines():
        if not (re.search(r"coupon", ln, re.I) and re.search(r"seat", ln, re.I)):
            continue
        if REMOVE_RE.search(ln) or ADD_SSR_RE.search(ln):
            continue
        if any(rx.search(ln) for _, rx, _ in PATTERNS):
            continue
        out.append(" ".join(ln.split()))
    return out[:50]
