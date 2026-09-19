---
name: planning-travel-itineraries
description: Use when turning saved places, destination ideas, or an existing trip note into a researched itinerary where interests, opening schedules, transportation, geographic routing, lodging bases, and limited time must be reconciled.
---

# Planning Travel Itineraries

Build a feasible itinerary that spends scarce time on experiences that best express the destination. Popularity is evidence, not the objective function.

## Interest profile

Read [references/interest-profile.md](references/interest-profile.md). Apply explicit trip-specific preferences first; otherwise use its defaults.

Do not treat a preference learned for one destination as universal. Determine the destination's defining strengths from authoritative tourism sources, then adjust category weights. A culture-and-food destination may justify more museums and markets; a destination known for scenery and hiking should elevate outdoor time.

## Research and selection

1. Establish dates, arrival/departure constraints, fixed bookings, pace, mobility limits, lodging constraints, and interest overrides. Ask only for missing facts that would materially change the result.
2. Inventory candidates from the user's saved places plus official venue, transit, municipal, and national-tourism sources. Separate attractions from hotels, airports, restaurants, transit nodes, duplicates, closed places, and locations outside the trip boundary.
3. For each serious candidate, record location, category, current opening days/hours, public-holiday and seasonal closures, timed-entry needs, visit duration as a range, and source date from the official sources, then look up its current map rating and review count with the lookup date.
4. Sort the audit table by review count, then rating, unless the user asks for another order. Popularity informs the decision but never forces a keep: the user's preferences and the destination's defining strengths decide the final list. Cut a highly rated stop when its category is neither a user interest nor a defining strength and a better-fitting stop can use the time.
5. Cluster candidates geographically. Build days around fixed opening windows, then add meals, explicit transfer time, and a realistic buffer. Use evenings for night attractions or longer transfers when that preserves daytime attraction hours.
6. Select transport per destination and segment. Favor walking, metro, rail, and rideshare in dense transit-rich cities; evaluate rental cars in dispersed or road-trip-oriented regions such as much of the United States; use other modes when locally superior. Never carry one country's transport assumptions into another.
7. Minimize backtracking and unnecessary hotel changes. Compare the packing/check-in penalty with any travel time saved before changing bases.
8. Cut what cannot fit. State each cut or demotion and its reason. Protect high-value time rather than compressing every stop into an implausible schedule.

## Output contract

Produce enough evidence for the user to audit the plan:

- a dated candidate table with reviews, ratings, hours, duration range, and keep/cut decision;
- geographic clusters;
- a day-by-day schedule where every day shows attraction time, door-to-door travel time, meals, and buffer;
- transport and lodging-base rationale;
- assumptions, uncertain hours, and items to recheck shortly before travel;
- authoritative links adjacent to the claims they support.

When editing an existing note, preserve raw or popularity-only tables unless replacement is explicitly requested. Reconcile summaries, cut lists, hotel advice, and transport notes with the final itinerary. Obtain approval before finalizing a materially reworked itinerary. Commit or push only when requested, stage only intended files, and verify the remote result.

## Privacy

Never place personally identifiable information in this skill directory, its examples, evaluations, or reusable templates. Exclude names, contact details, home addresses, account or loyalty identifiers, reservation codes, and private travel-history lists. Use generic placeholders and generalized preferences; test fixtures may contain only obviously fake values. If source material contains such data, do not reproduce it in research summaries, examples, test fixtures, or commit messages.

## Common mistakes

- Treating high review volume as an automatic keep decision.
- Omitting inter-attraction travel time or counting it twice.
- Scheduling a market, museum, ferry, or ceremony on the wrong day.
- Calling outdoors low priority because it was low priority on a different trip.
- Assuming public transit or rental cars are universally best.
- Adding stops after approval without reconciling every summary and cut list.
