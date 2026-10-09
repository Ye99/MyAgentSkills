# Fengjia food-stall video handoff

Updated 2026-10-09. Continue the user's paused request to process [吃貨豪豪 HowHowEat's 逢甲夜市 video](https://www.youtube.com/watch?v=McW0pQXA83w) (29:37; published 2024-03-03, filmed 2024-01-09). Follow [the video-to-Maps skill](video-food-places-to-google-maps/SKILL.md).

## Current state

- The video's expanded description has no stall names or addresses. YouTube reports no transcript, and captions are unavailable. Skip detailed dish or endorsement research under the user's instruction; do not turn viewer opinions into creator recommendations.
- No Google Maps save or note was changed for this video. No Source 4 text or stall row was added to `/home/ye/p/Notes/2026Taiwan.md`. Its food table currently has 27 rows from three earlier videos; the Notes file also has unrelated uncommitted edits that must be preserved.
- The user asked to pause video processing to save tokens. Later, they specifically authorized updating the skill and committing/pushing it, plus this handoff. Resume video processing only on a new request.

## Leads observed so far

These are **leads, not verified Maps matches or a complete stall list**:

| Approximate video time | Evidence | Next check |
| --- | --- | --- |
| 0:30–3:51 | Indoor restaurant wall includes 當歸鴨. A viewer says the first place is known for pig's feet as well as 當歸鴨. | Read the full on-screen storefront/name and match the exact business on Maps. |
| 4:03–5:51 | New orange storefront has a name ending in 家 and advertises 豆乳雞; likely 島祿家豆乳雞. | Confirm the complete on-screen name. 島祿家 is already a row in `2026Taiwan.md` and previously saved to Taiwan; append this source only after confirming identity, without toggling Save. |
| About 6:15 | The group eats a stuffed bun. A viewer calls a visited bun stall 舞揚 and mentions 鮮肉包. | Locate its on-screen title and current Maps listing. Do not assume the wrapper or food alone identifies it. |
| About 11:19 | On-screen dish label says 福建麵; the takeaway box's business name was not read. | Find the preceding stall introduction and exact name. |
| About 19:37–20:07 | A viewer specifically names 海邊小屋 at 19:37; the scene around 20:07 shows its seafood cup. | Confirm its on-screen name. 海邊小屋 is already a row in the travel note and saved; add a repeat-video mention and append to its existing Maps note, never click Save again. |
| Times unknown | Viewers mention 一心素食臭豆腐 and an 逢甲起司蛋餅 stall. | Locate their on-screen scenes; 一心 is an existing saved row. Do not confuse a viewer's negative opinion of 一心 with the creator's opinion. |

## Efficient continuation

The video's stall names appear visually at transitions. Sampling separate times, then narrowing an interval by binary search worked: 3:51 still showed the first indoor restaurant; 4:03 showed the next storefront. Inspect the frame after the seek finishes decoding, because the first screenshot sometimes still shows the previous frame. The YouTube seek bar may move vertically as the page scrolls, so read its current UI position before each click. Seek within the existing video tab when possible; repeated page navigations caused ads.

Complete the full sequence of stops and check each exact name and address against current Google Maps, including closure status, rating, review count, and existing Taiwan-list membership. Skip permanently closed or unresolved listings. For existing saves, append the new video URL to the Maps note without clicking Save. Add Source 4 and source links to existing or new rows in `2026Taiwan.md`, preserving concurrent edits. Verify Maps notes after autosave and reread the note diff before reporting newly saved versus already saved, closed, and unresolved counts.
