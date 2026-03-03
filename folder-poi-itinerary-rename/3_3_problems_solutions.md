# 3_3 Problems and Solutions

## Problem 1: Early trimming lost valid itinerary stops

- **Observed issue:** Important stops were removed before the final selection stage, producing too few names and unstable outputs.
- **Root cause:** Landmark list was effectively constrained too early, before full-day context was considered.
- **Solution implemented:** Final selection now happens in second pass with full itinerary context, and second pass receives location-set member metadata (`set_member_count`, `itinerary_order`) to trim smallest sets first only when needed.

## Problem 2: Generic names selected over specific names

- **Observed issue:** Generic labels such as `InformationCenter` or `VisitorCentre` could appear in final names.
- **Root cause:** Prompts did not explicitly disambiguate "specific + qualifier" vs "generic-only" information-center labels.
- **Solution implemented:** Updated prompts to explicitly prefer specific labels (example included: `Statue of Liberty Information Center`) and reject generic-only labels (example: plain `Information Center`).

## Problem 3: Inconsistent naming and dead code paths

- **Observed issue:** Legacy pass-1 selection code remained, and naming mixed `tag` vs `landmark` terms.
- **Root cause:** Runtime flow had evolved to second-pass-only selection, but pass-1 and legacy naming persisted.
- **Solution implemented:** Removed pass-1 selection functions and related tests; renamed CLI/internal filter naming from `tag` to `landmark_filter` (while still sending LocationIQ API param `tag` externally as required).

## Additional notes for reviewer

- Dual-source grouping integrity remains per location set in `_assign_labels(...)`; candidate merge is per centroid, not cross-set.
- Added tests to confirm no cross-set candidate mixing.
- Sampling default changed from `0.6` to `1.0` (100% files).
