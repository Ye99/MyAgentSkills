---
name: find-local-food
description: Find and verify good local restaurants and food stalls for a trip, including Michelin Bib Gourmand and historical winners, then fit eligible places into meals and optionally save them in Google Maps. Use for destination food research or an itinerary food audit.
---

# Find local food

Build an evidence-backed food plan around the places the traveler will actually visit. Verify the business and visit timing before making a recommendation actionable.

## Scope and preferences

Read the supplied itinerary or destination context first. Reuse stated preferences and ask only for missing information that materially changes the choices. Establish:

- Neighborhoods, meal windows, travel dates, acceptable detours, budget and dietary needs.
- Whether awards should take priority over existing meals, and whether a market crawl should include every eligible stall or a shortlist.
- Current awards only, historical awards as well, or ordinary local food recommendations. Michelin Bib Gourmand is an optional discovery mode, not a prerequisite for good local food.
- Any Maps rating cutoff and its scope: all places, historical winners only, or another category. Preserve the exact boundary; if the cutoff is 4.0, a 4.0 rating qualifies. Do not impose a cutoff the user has not chosen.
- Research only, itinerary edits, Maps saves/notes, or an independent audit. Research does not itself authorize account changes.

## Find candidates efficiently

1. Cluster the existing route into meal areas. Search within those areas before proposing detours; do not collect an entire city's restaurants for a short visit.
2. Batch independent searches by neighborhood and source. Prefer official award guides, tourism authorities, market directories and restaurant menus for names, identity, hours and dishes. Independent food reporting can supply additional leads; distinguish editorial recommendations from awards and restaurant marketing.
3. Find foods characteristic of the destination as well as well-supported businesses serving them. Rank eligible choices by local relevance, food evidence, price, review confidence and route fit. Rating and review volume inform confidence; neither proves quality by itself.
4. Verify only serious candidates before expanding the search. Stop when the requested meal areas and candidate coverage have been addressed. If the user requests every eligible winner or a historical survey, record the coverage and finish that scope rather than silently turning it into a shortlist.

For recommendations outside Michelin-covered areas, use the same identity and timing checks with local sources. If a video is the main source, the sibling `video-food-places-to-google-maps` skill can support extracting the named businesses; keep source mentions separate from actual endorsements.

## Michelin and historical awards

- Search the destination's official Michelin annual Bib list and current Bib category. Learn the destination's local-language term from Michelin; for Taiwan, use **必比登推介** and Traditional Chinese names.
- Verify the latest published edition. The current calendar year, an article's update date, or a restaurant page's copyright year does not establish its award year.
- An individual Michelin listing can be **Selected**, starred, or Bib Gourmand. Verify the distinction explicitly rather than treating every Michelin restaurant as Bib.
- Prove historical awards with the original annual list or a dated Michelin announcement. Record a documented award year separately from current status. A historical winner may remain eligible under the user's rules even after losing Bib status.
- For a requested recent N-year window ending in year Y, cover Y−N+1 through Y, limited by when the guide began covering the area. Track each edition as checked, unavailable, or outside coverage. Do not claim an exhaustive survey after checking a few years.
- Apply filters after confirming current versus historical status. Include all eligible businesses in a shared market when requested, rather than picking one representative.

### Repeatable Michelin method

1. **Current guide data.** Michelin's site is backed by a public search index. To find it, read the network requests on a Michelin search page in a browser. Each record gives the award (Bib vs Selected), street address, phone, opening hours and coordinates. Query it per city at a polite rate, and save the full result set to a scratch file for distance work. Plain HTTP fetches of Michelin pages may receive a bot challenge. Fetch them from inside a normal browser page instead, and do not bypass CAPTCHAs.
2. **Historical coverage.** Build a year-by-year union of winners: the first edition's full list (often a PDF), each later year's "new Bib Gourmand" announcement, and the current category pages. Keep a coverage table per city and year (checked, unavailable or outside coverage).
3. **Route fit.** For every winner, compute the straight-line distance to each planned meal point (hotel, sight, market). Shortlist those within the user's detour limit, or a short walk (about 600 m) if none was given. Then check that each is open on that **weekday** at that meal time. Compute each date's weekday with code rather than from memory. A weekly closure on the planned day is the most common failure, so check it for existing stops as well as new ones; a day swap may fix it.
4. **Historical winners.** Apply the user's rating cutoff to the exact Maps listing. Check for closures and relocations: a permanently closed listing, a moved shop (new address, perhaps off route) and an unrelated shop at the old address are three different outcomes.
5. **Travel legs.** Measure new or changed legs for the actual trip weekday and departure time with the sibling `google-maps-travel-times` skill. It pins each stop to its exact Maps place and encodes "Depart at" in the URL, giving typical driving ranges, real transit departures, and bike and walking times. A plain `maps/dir/?api=1` link only shows current traffic, and a name search can open the wrong place.

## Verify the exact business

Match the local name **and exact branch/address** between the source and the live Maps place page. Use official phone numbers, storefronts, market stall numbers and coordinates when names or addresses are ambiguous.

- Read the live place rating, review count, closure status and current address; record the lookup date and Maps place URL.
- Distinguish a moved business, a similarly named branch, and a different business at the old address. Resolve an address-number discrepancy with corroborating evidence; do not silently substitute the Maps address for the source address. Three checks resolve most cases:
  - The phone number matches.
  - The source's pin and the Maps pin are within a few tens of metres.
  - A Maps search with the source's number returns the same listing.

  Record both numbers in the output.
- An unsigned cart or market stall may lack a street number. Record its verified landmark/stall identification. Keep a guessed or unresolved Maps match out of saved selections.
- Use restaurant or tourism sources for visit-day opening hours, weekly closures and sell-outs. Cross-check Maps when available and label conflicts. Include queue and travel buffers in the meal plan.

## Make the plan usable

Use local-language names and menu dish names, with a short translation when helpful. Distinguish Michelin's cuisine category from descriptive specialties added for lookup. Summarize dishes the source actually highlights; do not invent recommendations from the restaurant's name. Leave an explicit information gap when no dish evidence is available.

Keep one row per business/branch and link sources near the claims they support. A useful stop table is:

| Meal / area | Local name + Maps place URL | Cuisine Type | Dishes | Address + visit hours | Award / recommendation source | Maps rating (reviews) + checked date |
| --- | --- | --- | --- | --- | --- | --- |

Only include columns that serve the task. For historical awards, show both the award year and current classification. Record excluded or unresolved candidates and the reason separately from active meal choices.

When editing an itinerary, update the main day schedule, breakfast/lunch/dinner lookup and stop directory together. If the user gives awards priority, replace the conflicting meal and account for the detour. Preserve unrelated plans and useful fallbacks. A market crawl must allow enough time to visit all listed stalls.

## Optional Google Maps work

Read [references/google-maps.md](references/google-maps.md) only when saving, removing, annotating or auditing places is requested. Use the user's intended browser/list, inspect existing membership before changing it, preserve existing notes, and verify the full saved note after persistence. The reference also covers browser automation, stable place links (`maps?cid=`) and the final list audit.

## Verification and handoff

Before reporting completion, check that business names, branches, award years, filters, dishes and visit timing agree across the outputs. Check table structure and links after document edits.

For a review or unfinished pass, record:

- Selection rules and date/edition coverage, including user changes that superseded earlier choices.
- Source URLs, verified identity evidence, rating lookup dates and unresolved conflicts.
- Per-place Maps state: newly saved, already saved, removed, unresolved or not checked; full-note verification status when relevant.
- Replacements and excluded candidates, with reasons and route implications.
- Remaining checks and a concrete review request. Distinguish live observations from a summary of earlier work; do not turn an attempted action or aggregate toast count into a completion claim.

## Privacy

Keep this skill, supporting files, examples and validation fixtures free of PII and private trip data. Do not copy names of travelers, contact details, home addresses, account identifiers, reservation codes, private list links, session IDs, trip dates or lodging history into reusable assets or commit messages. Use generic placeholders. Public restaurant identities and official business sources are research data, but prefer generic examples in the skill.

Use private trip context only in the task's authorized working output. Maps notes should describe the business, food and evidence by default; include personal scheduling details only when the user explicitly requests them. Never copy a full private itinerary or signed-in account information into a Maps note.
