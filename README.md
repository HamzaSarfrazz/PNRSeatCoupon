# PNR Seat Assignment & Change Report

Streamlit app that reads a PNR Admin page, finds the **reservation date** and the
**payment date** on the page itself, keeps the Reservation History between those two
moments, and reports each coupon's initial seat and the seat it was changed to.

## Setup

    pip install -r requirements.txt
    streamlit run app.py

## Use

1. Open the PNR, select everything (Ctrl+A), copy, paste into the sidebar box —
   or save the page as `.txt` and upload it.
2. The two dates appear at the top. For PNR KFBSPZ that is
   **30-Jun-2026 11:17 PM** (Ticket Time / installment payment) and
   **4-Aug-2026 2:52 PM** (Ticket Sale). Untick *Use the dates found on the page*
   to set a different range.
3. Read the table, then **Download Excel** or **Download CSV**.

## Where the dates come from

| Value | Source on the page |
|---|---|
| Reservation date | `Ticket Time 30-Jun-2026 11:17 PM`, falling back to the *Installment Payment* row of the Payment Center Log |
| Payment date | the *Ticket Sale* row of the Travel Agency / Payment Center Log (4-Aug-2026 2:52 PM) |

## Columns

| Column | Meaning |
|---|---|
| S No | Row number |
| Coupon Number | Coupon ID from the log |
| Initial Assignment Date | Date of the coupon's first seat entry in the range |
| Initial Seat | Seat at that first entry |
| Seat Change Date | Date of the entry that moved the seat |
| New Seat | Seat after the change |
| Status | Changed / Unchanged |

Switch to **Full seat event log** for every individual entry with timestamp, user and
the original log text.

## Log formats recognised

- `Assign seat 30F to coupon 86901522`
- `Coupon: 86901605 seat:28D is assigned.`
- `Change seat from 23C to 23F on coupon 86901606`
- `Seat reassignment for coupon 86901605: 25C to 28D`
- `Change Seat for Coupon 86901549: 37E to 29B` (and the `: to 29C` variant)

`SEAT added to coupon ID# …` / `SSR Removed` rows are ignored — they don't move a seat.
Each website reassignment is logged twice at the same second; the duplicate is dropped.

## Parsing notes

The page is flattened and split at every date/time stamp, so the parser doesn't depend
on tabs, table columns or line breaks. The **Diagnostics** panel in the app lists any
line that mentions a coupon and a seat but produced no entry — if something is still
missing, that panel will show it.

`sample.txt` is a cut-down PNR you can upload to check the app end to end.
