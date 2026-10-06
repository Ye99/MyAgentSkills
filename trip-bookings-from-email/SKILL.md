---
name: trip-bookings-from-email
description: Reconcile a trip-planning note with the booking emails in the user's mailbox, recording only bookings that are still active. Pairs each confirmation with any later cancellation or change by confirmation number, so booked-then-canceled reservations are left out. Use when the user asks to "update bookings", "check my email for reservations", "sync my itinerary with my confirmations", or similar, and an email connector (e.g. a Gmail MCP server) is available.
---

# Trip Bookings from Email

A trip note drifts from reality as the traveller books, rebooks, and cancels.
The mailbox is the source of truth, but it holds every version of every
booking. This skill finds the bookings that are **still active**, and records
them in the note without copying secrets into it.

## Ground rules

- **Latest event wins.** A reservation is active only if its most recent email
  is a confirmation or a change, not a cancellation. Never record a canceled
  booking. Mention it in the report so the user knows it was seen and skipped.
- **Match by confirmation number, not hotel name or date.** The same property
  can be booked twice, and two properties can share a date.
- **No secrets in the note.** Leave out PINs, booking-management or check-in
  links, full card numbers, and passport numbers. Point to the confirmation
  email instead ("PIN in the confirmation email"). A note may be read by
  people or tools the confirmation email never reaches.
- **Read the full email body before recording.** Snippets drop the
  cancellation deadline, the price breakdown, and whether payment is at the
  property.
- **Change only what the bookings affect.** Do not rewrite plans, prices, or
  sections the emails don't touch.

## Workflow

### 1. Read the note first

Find what the note already records: the trip dates, the cities and nights, any
"booked" markers, confirmation numbers, the checklist, the hotel and flight
tables, and any cost totals. This tells you what to search for and what is
already done.

### 2. Search the mailbox

Generic keyword searches ("booking", "confirmation") return mostly
newsletters. Search by **travel sender** and **trip-specific terms** instead,
limited to the booking window (e.g. `newer_than:60d`):

- Booking sites and airlines, e.g. `from:booking.com OR from:agoda.com OR
  from:trip.com OR from:expedia.com OR from:<airline domain>`.
- The names of the hotels, airlines, rail operators, and attractions in the
  note, in every language the note uses (e.g. the Chinese name of a hotel).
- `cancel OR canceled OR cancelled OR cancellation`, plus the local-language
  words, to catch cancellations whose subject doesn't name the property.
- Exclude promotional senders and subjects (`-subject:(deals OR sale OR offer)`).

Also look in **Sent** for bookings the user forwarded, and note when a
confirmation says it was sent to a **different address**: that mailbox can't
be read, so keep whatever the note already says and flag it.

### 3. Build the booking ledger

For each confirmation number, list every email in date order and keep the
latest state:

| Confirmation # | Provider | Item | Dates | Events (oldest → newest) | Final state |
| --- | --- | --- | --- | --- | --- |

Final state is **active**, **canceled**, or **changed** (record the new
details). Read the full body of every active booking and capture: property or
carrier, address, dates and times, room or fare type, total price and currency
(say whether tax and fees are included), whether payment is now or at the
property, the free-cancellation deadline with its time zone, and the
cancellation fee after that.

### 4. Update the note

For each active booking that isn't recorded yet, or whose details changed:

- Tick the checklist item and add a short booked line: provider, confirmation
  number, price, payment timing, cancellation deadline.
- Mark the row in the hotels or flights table as booked.
- Replace any "options" or "candidates" box for that slot with the booked
  details.
- Name the property in the day plan where it was a placeholder ("one-night
  hotel").
- Recompute any cost subtotal and total the booking changes. Convert the
  currency at the current rate and label it approximate ("~$68").

Leave items with no confirmation unticked.

### 5. Report

Tell the user:

- **Added:** each new active booking and its key terms, especially the
  cancellation deadline.
- **Skipped as canceled:** each booked-then-canceled reservation, with its
  confirmation number.
- **Already recorded / still open:** what was already in the note, and which
  planned items have no confirmation yet.
- **Couldn't check:** confirmations sent to another mailbox.
- **What was left out of the note on purpose** (PINs, links), and where to find it.

