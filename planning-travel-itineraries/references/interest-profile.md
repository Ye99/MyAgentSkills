# Configurable Interest Profile

Use explicit per-trip instructions as overrides. When none are supplied, begin with this non-identifying default profile and adapt it to the destination.

| Dimension | Default |
| --- | --- |
| Museums and history | High |
| Local food and food markets | High |
| Destination-distinctive culture and architecture | High |
| Outdoors, scenery, and hiking | Adaptive: high when central to the destination; otherwise medium |
| Generic city parks or duplicated scenery | Low unless unusually distinctive |
| Shopping | Low unless culturally distinctive or explicitly requested |
| Pace | Medium-fast; maximize useful hours without turning visits into checklists |
| Nights | Use for night markets, illuminated sights, performances, waterfronts, or transfers |
| Routing | Minimize backtracking; include door-to-door travel time |
| Lodging | Economical and clean; avoid base changes unless they save more time than packing, transfer, and check-in cost |
| Transport | Adaptive to local geography and infrastructure |

## Destination-strength adjustment

1. Use authoritative destination sources to identify two to four defining experience categories.
2. Raise those categories by one priority level, but only for dimensions the user has not set explicitly, and not when the user has already experienced close substitutes.
3. Lower categories that are not defining strengths, again only for dimensions the user has not set explicitly. Cut generic, duplicated, or non-defining stops, even highly rated ones, before cutting a defining experience.
4. Ask about comparable prior experiences only when the answer would materially change a major allocation. Do not store the resulting personal travel history in the skill.

Examples:

- Taiwan is best known for history, museums, temples, and food markets rather than outdoor sightseeing, so a popular mountain park near the capital can be cut in favor of cultural stops unless the user asks for hiking.
- In Norway, fjords, scenic routes, hiking, and other outdoor experiences should normally receive high priority because they are central to the destination.

## Transport adjustment

Choose mode by the actual trip rather than by the default profile:

- Dense, transit-rich city: compare walking, metro, rail, bus, and rideshare.
- Dispersed United States itinerary or scenic road trip: evaluate a rental car, parking, one-way fees, and driving time.
- Intercity corridor: compare rail, coach, flight, and car door to door.
- Remote outdoor area: verify trailhead access, seasonal roads, ferries, and the last practical return.

Record the selected mode and travel allowance for every material transfer.

## Override format

Accept natural language or a compact structure such as:

```yaml
interests:
  outdoors_hiking: high
  museums_history: medium
  food_markets: high
pace: medium-fast
transport:
  rental_car: allowed
lodging:
  max_base_changes: 2
```

Missing fields inherit the defaults; explicit user choices always win.
