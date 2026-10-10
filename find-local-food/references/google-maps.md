# Google Maps saves, notes and audit

Use this only for authorized Maps work. Discover relevant connectors first; if they cannot inspect or change the requested list, use supported browser automation in the user's intended signed-in browser. Honor an explicitly chosen browser and list. Read its current documentation; do not assume a previous session's tab IDs or element indices are reusable.

## Per-place workflow

1. Open the verified business. After a search, wait for the expected restaurant heading and address before reading its rating or clicking controls. A new query can coexist briefly with the previous place's content; a rapid loop over that stale content is not verification.
2. Confirm local name, branch/address, rating, review count and closure status. Resolve conflicts before saving. Record the public Maps place URL in the task output.
3. Inspect membership in the requested list. **Save and list checkboxes can toggle an already-saved place off.** If it already belongs to the list, keep its membership and edit the note only as needed. If absent and eligible, save once and verify the requested list is selected.
4. Inspect any existing note. Preserve unrelated content and avoid duplicating the same source. Append or narrowly update a concise sourced note; do not replace the whole field just to add an award.
5. Wait for the note to persist using an observable UI change, such as Add note becoming Edit note or the editor returning to the saved state. Reopen the saved-note control and verify the **full text**, target list and correct place. A collapsed preview alone cannot prove the full note survived.
6. For an authorized exclusion/removal, remove membership only from the requested list. Verify the resulting unchecked list state or a specific removal confirmation. Do not remove unrelated saves or reviews.

## Browser and session

- The browser doing the work must be signed in to the account that owns the list. A separate automation profile that is not signed in cannot save to the user's list, so do not fall back to one. If the intended browser can't be reached or isn't signed in, ask the user to connect it, sign in, or import their session. Never type or handle their credentials.
- Treat the signed-in session as the user's: never sign out or change account settings.

## Automation that held up (Maps web UI, 2026)

These selectors were observed on the English Maps UI and may change; confirm them on a live page first.

- **Place page fields:** name in `h1.DUwDvf`; rating in `div.F7nice`; address in `button[data-item-id="address"]`; phone in `button[data-item-id^="phone"]`.
- **Save state:** read the Save button's `aria-label`. `Save` means not saved anywhere. `Saved` means saved to at least one list, possibly a different one. Open the picker and read the target list's checkbox state before clicking anything. In the picker, click the visible leaf element whose exact text is the list name. Search only inside the open menu or dialog, because the same word can appear elsewhere on the page, for example in an address.
- **Search can return a list:** a search may show results instead of one place page. Then open the result whose address matches; never act on the first result blindly.
- **One guarded script per place:** wait for the page, then check that the address or phone contains an expected substring. If the Save button is not `Save`, stop the automated path and handle the place by hand as described under Save state. Otherwise click Save, then the list name, then wait. Confirm the page text shows "Saved in <list>". Read the visible `textarea`'s current value before typing:
  - If it is empty, focus it and type the note.
  - If it already has text, only select-and-replace when the new text deliberately keeps the old content (step 4 above). Otherwise put the cursor at the end and append.
  - Type with real key input, then press Tab to commit the note.
  - Clicking the note field by screen coordinates proved unreliable; focusing it with JS and typing worked.
- **When a check fails, look before retrying.** A confirmation check that runs too early can report failure even though the save happened. Take a screenshot or re-read the Save state before acting again; a second Save click would remove the place.
- **Keep calls short.** Tool calls time out and batches have action limits. Split loops into chunks that finish well under the timeout. Save per-item progress somewhere that outlives a call (for example a temporary `localStorage` key) and resume from it. Delete that key afterwards.

## Stable place links

A search URL is not reproducible. Take the place's feature ID from the place URL: the `!1s0x<hex>:0x<hex>` segment. Convert the **second** hex number to decimal with code, never by hand: a mistyped digit gives a link that opens the wrong place. That decimal is the place's CID, and `https://www.google.com/maps?cid=<decimal>` opens that place. This mapping was observed, not documented, so test it. To collect links for an existing list:

1. In the list view, click each entry's button and read the feature ID, name and address from the place page.
2. Go back with `history.back()` and wait until the list re-renders.
3. Match each entry to the output table by name, then check its address.
4. Open at least one generated link to confirm it lands on the right place.

## Final list audit

Open the list and find its scrollable panel: the element with the largest `scrollHeight` whose `overflow-y` is auto or scroll. Scroll it to the bottom until its height stops growing, so every entry is loaded. Check that the number of loaded entries equals the list's "N places" count. Then pair each note `textarea` with its entry's `.fontHeadlineSmall` place name by walking up the textarea's ancestors. Skip the list's own description box (`textarea[aria-label="List description"]`); otherwise it gets paired with the first place as an empty note. Compare these rows with the target set:

- Every target is present, with a full note.
- No excluded business is present. Watch for a different branch with a similar name.
- Note, but do not edit, unrelated or closed entries the user saved earlier, and report them.

## Note content

For a confirmed Bib winner, start with the user-requested wording, for example:

> Michelin’s <documented award year> Bib Gourmand. <Cuisine / sourced dishes>. <Official Michelin URL>.

For a historical winner, optionally add its current classification when verified so the note does not imply it still holds Bib status. For other food recommendations, name the source and the dishes it actually recommends. Add a planned meal/date only when requested. Do not place account details, reservations, lodging, companions or private travel history in the note.

Use plain text in Maps notes. Markdown emphasis may display literally. Keep full official URLs and meaningful award years; do not use the current year merely because the page was checked this year.

## Completion ledger

Track membership and note persistence separately, with one row per business:

| Business / branch | Maps place URL | Identity / rating checked | List state | Full note verified | Remaining issue |
| --- | --- | --- | --- | --- | --- |

Useful list states are newly saved, already saved, removed, unresolved and not checked. Do not infer per-place status from multiple accumulated “Saved to list” toasts or from a changing total list count. Do not click Save just to verify it.

When authorized batch work completes, report counts from the ledger, separating newly saved from already saved and excluded/unresolved places. If interrupted, preserve the specific remaining steps in the task's private note rather than the reusable skill. Respect a request to stop or pause, and do not claim that the batch is complete while rows remain unverified.
