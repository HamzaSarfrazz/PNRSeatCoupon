# PNR Seat Assignment & Change Report

Streamlit app that reads a PNR Admin page, finds the **reservation date** and the
**payment date** on the page itself, keeps the Reservation History between those two
moments, reports each coupon's initial seat vs. the seat it was changed to, and flags
any coupon where the same staff member or travel agent removed a seat and then
reassigned it themselves shortly after.

## Setup

    pip install -r requirements.txt
    streamlit run app.py

## Use

1. Open the PNR, select everything (Ctrl+A), copy, paste into the sidebar box —
   or save the page as `.txt` and upload it.
2. The two dates appear at the top, read straight from the page. Untick
   *Use the dates found on the page* to set a different range.
3. Read the table, review the flagged panel if it appears, then **Download Excel**
   or **Download CSV**.

## Where the dates come from

| Value | Source on the page |
|---|---|
| Reservation date | `Ticket Time`, falling back to the *Installment Payment* row of the Payment Center Log |
| Payment date | the *Ticket Sale* row of the Travel Agency / Payment Center Log |

## Who did it

The Reservation History's `User Type` column is turned into a role:

| User Type | User Name | Role |
|---|---|---|
| `PA` | any | **Employee** (airline staff) |
| `Agent` | the airline's own website/app (e.g. "Airblue Website") | **System / Self-Service** — not a person acting on the booking |
| `Agent` | anyone else | **Travel Agent** |

## Flagged: same-actor seat removal + reassignment

If the same Employee or Travel Agent both:
1. removed a coupon's seat (`SEAT removed from coupon ID# ...`), and
2. assigned that coupon a *different* seat afterwards,

the coupon appears in a warning panel above the table, with who did it, when, and the
seat before/after. Website/self-service reassignments are never flagged this way —
only staff-side actions are, since that's the pattern worth a second look (e.g.
moving someone to a cheaper seat ahead of an aircraft change, then claiming
compensation for the original seat later). The flag is a signal to review, not a
finding on its own — the report doesn't judge intent.

## Columns

**Summary**

| Column | Meaning |
|---|---|
| S No | Row number |
| Coupon Number | Coupon ID from the log |
| Initial Assignment Date | Date of the coupon's first seat entry in the range |
| Initial Seat | Seat at that first entry |
| Seat Change Date | Date of the entry that moved the seat |
| New Seat | Seat after the change |
| Status | Changed / Unchanged |

**Full seat event log** adds Event, From Seat, To Seat, User and Role per entry.

**Flagged Reassignments** (separate Excel sheet / panel): Coupon Number, User, Role,
Seat Removed At, Reassigned At, Previous Seat, New Seat.

## Log formats recognised

- `Assign seat 30F to coupon 86901522`
- `Coupon: 86901605 seat:28D is assigned.`
- `Change seat from 23C to 23F on coupon 86901606`
- `Seat reassignment for coupon 86901605: 25C to 28D`
- `Change Seat for Coupon 86901549: 37E to 29B` (and the `: to 29C` variant)
- `SEAT removed from coupon ID# ...` / `SEAT added to coupon ID# ...` — used only for
  the flag above, not counted as a seat movement on their own.

Each website reassignment is logged twice at the same second; the duplicate is
dropped so a change is counted once.

## Parsing notes

The page is flattened and split at every date/time stamp, so the parser doesn't
depend on tabs, table columns or line breaks. The **Diagnostics** panel in the app
lists any line that mentions a coupon and a seat but produced no entry.

`sample.txt` is a cut-down PNR you can upload to check the app end to end, including
a same-actor remove-then-reassign case and a website reassignment that should NOT
be flagged.
