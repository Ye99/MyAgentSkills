---
name: google-maps-travel-times
description: Use when an itinerary needs realistic travel times for future trip days by car, public transit, bike or walking. Gets Google Maps estimates for the actual weekday and departure time (typical traffic ranges, real transit departures and fares), with every stop pinned to its exact Maps place.
---

# Google Maps travel times for trip days

Live directions describe traffic right now. For a trip day, ask Google Maps for that **weekday and departure time** instead. Maps then returns:
- **Driving:** a typical range, for example "typically 14–35 min".
- **Transit:** that day's scheduled departures, lines and fare.
- **Bike and walking:** time-independent, but the same method returns them.

## How it works

A directions URL can carry each stop's exact place and the departure time:

```
https://www.google.com/maps/dir/<lat>,<lng>/<lat>,<lng>/data=!4m18!4m17
  !1m5!1m1!1s<featureId>!2m2!1d<lng>!2d<lat>     origin
  !1m5!1m1!1s<featureId>!2m2!1d<lng>!2d<lat>     destination
  !2m3!6e0!7e2!8j<seconds>                       6e0 = depart at, 6e1 = arrive by
  !3e<mode>                                      0 drive, 1 bike, 2 walk, 3 transit
```

- `<seconds>` is the **local wall-clock time written as if it were UTC**, e.g. `calendar.timegm((Y, M, D, 10, 50, 0))` for 10:50 local time on that date. Maps then shows "Depart at 10:50 AM" with that weekday and date.
- `<featureId>` is the `0x…:0x…` pair from a place URL's `!1s…` segment. Coordinates come from its `!3d<lat>!4d<lng>`.
- Pin every stop by place. A name search can open a different place: "見城館 高雄市左營區" once resolved to a nearby gun battery, which skews the time.

These URL fields were observed in 2026, not documented by Google. Before trusting a batch, open one generated URL and confirm on screen that the panel shows the intended **date, time, mode and both places**.

## Workflow

The helper is [scripts/gmaps_times.py](scripts/gmaps_times.py). It writes browser batches for a browser automation tool that can run a `navigate` step and an in-page JavaScript step. Results are stored in the page's `localStorage` (`gmt_places`, `gmt_legs`), so a batch that times out loses nothing.

1. **Write `legs.json`** in a scratch directory (never in a reusable repo):
   ```json
   {"places": {"hotel": {"query": "<hotel name> <street address>"},
               "museum": {"query": "高雄市立歷史博物館"},
               "zuoying": {"query": "高鐵左營站"}, "taoyuan": {"query": "高鐵桃園站"}},
    "legs": [{"label": "day 3 14:25 hotel→museum", "from": "hotel", "to": "museum",
              "mode": "drive", "depart": "YYYY-MM-DDT14:25"},
             {"label": "day 4 HSR", "from": "zuoying", "to": "taoyuan", "mode": "transit",
              "arrive": "YYYY-MM-DDT14:40"}]}
   ```
   - Use one leg per actual movement in the itinerary, at its planned time. Choose the mode the plan uses; add a second leg for a realistic alternative, such as transit vs. taxi.
   - Use `arrive` for a fixed deadline, such as a train or a ticketed entry.
   - A place that is already resolved can carry `id`, `lat` and `lng` directly and skip searching.
2. **Resolve places.** Run `gmaps_times.py resolve-actions legs.json OUT`. On a Google Maps page, run `OUT/setup.json` once; it stores the two helpers in `localStorage`. Then run each `OUT/resolve*.json` file as a browser batch.
   - Read every returned line, comparing the name and address with the intended stop **and with what the itinerary says about it**. A business can have only one listing somewhere else: a seafood shop described as being by a ferry pier resolved to its only branch 5 km away. That is an itinerary error to report, not a lookup to force.
   - An empty line means no place page or result list appeared. Retry with a more specific query (add the district or a landmark).
   - `map centre` means Maps opened the place without exposing its feature ID. The coordinates are usually good enough, but the directions panel then names the nearest business instead of the place; check the distance looks right.
   - Fix a wrong match by making its query more specific (add the street address), or by pasting its `id`, `lat` and `lng`.
   - Collect the results with a JavaScript step that returns `localStorage.gmt_places`, save them as `places.json`, then run `merge-places legs.json places.json`.
3. **Measure legs.** Run `leg-actions legs.json OUT` and run each `OUT/legs*.json` file as a batch.
   - Keep batches small: the default is 6 legs (12 steps), well under one call's time budget.
   - If a call times out, check what is stored and continue.
   - Collect the results with `localStorage.gmt_legs` into `results.json`. For a hundred legs that is too large for one tool response: return it in slices, or have the page summarize each leg to one line (estimate, distance, schedule, fare).
4. **Report.** Run `report legs.json results.json` to get a Markdown table, then check any row marked `?` or **not measured** by opening its URL.
5. **Clean up.** Delete the four `localStorage` keys afterwards: `gmt_places`, `gmt_legs`, `gmt_resolve_fn` and `gmt_leg_fn`.

## Reading the results

- **Driving:** plan around the upper end of the "typically" range for tight connections; "Arrive around" also uses the slow end. The ranges come from historical traffic, so they are estimates, not guarantees.
- **Transit:** Maps lists real departures, lines and fare for that date. Timetables for distant dates may be projected from current schedules, so recheck about a week before travel. Intercity rail and buses may need the operator's own timetable.
  - Compare the **first departure Maps shows** with the planned time: a plan saying "train ~13:00" may really mean the 13:35, shifting everything after it.
  - Maps may route around a ferry or another service it doesn't model; take that leg's time from the operator.
- **Bike and walking:** time-independent. Bike mode may be unavailable in some regions; if Maps falls back to another mode, say so. For bike share, also note docking stations and the operator's fare.
- **Taxi or rideshare fare:** Maps doesn't estimate it. Estimate from the official local taxi meter rate and the measured distance, then label it as an estimate to confirm in the app.

When editing the itinerary, write the range (for example "typically 14–35 min") rather than a single number. Check that each following stop still fits at the slow end, and record the check date.

## Privacy

Keep trip files, place lists and results in a scratch or private notes directory. Reusable examples must not contain the traveler's addresses, booking details or account information. Treat the signed-in browser profile as the user's: don't change account settings, and delete the temporary `localStorage` keys.
