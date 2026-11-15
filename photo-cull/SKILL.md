---
name: photo-cull
description: Use when a folder of phone photos (for example a Google Takeout "Photos from YYYY" export of iPhone HEIC/JPG/PNG files) needs culling before archiving - removing screenshots, blurry shots, accidental shots, receipts and the frames of a burst where someone has closed eyes or a bad expression, and turning sideways or upside-down photos upright - with every proposed change shown to the user for review before anything is copied.
---

# Photo Cull

Proposes which photos in one flat folder to keep, shows the user every proposed change on a review
page, then copies the keepers to a new folder. **The source folder is never modified and nothing is
deleted.** Rejected photos simply stay behind in the source.

| Task | How it is decided |
|---|---|
| Screenshots | Metadata: EXIF `UserComment` or Takeout sidecar `description` is `Screenshot`, or the file name starts with `Screenshot`. No model. |
| Sideways / upside down | Two checks. Scene: a small classifier trained on the folder's own photos (most are upright, so rotating them gives free labels) on top of frozen DINOv2 features. Text: for photos that show text (museum signs, documents), EasyOCR reads the photo at all four rotations and the direction that reads cleanly wins. Readable text outranks the scene check. |
| Blurry | Laplacian sharpness per tile; a photo is blurry only if even its sharpest tiles are soft, so a sharp subject on a soft background passes. |
| Facial | Shots close in time whose every region matches (4x4 grid of DINOv2 features, the least similar region decides) form a burst. Comparing region by region keeps "same backdrop, different person" out of a burst. **Bursts are kept whole**, except frames with bad faces: a face detector (YuNet) finds the faces, MediaPipe scores each for closed eyes and smile, and each person is followed across the frames. A frame is dropped when someone in it has closed eyes or a much weaker expression than that person usually has in the other frames, and the frame is clearly worse overall than the best one. Only people the user knows are judged: a face recognition model (SFace) links faces that look alike across the folder, and a person seen on 3 or more different days is known; passers-by, statues and tour guides are strangers and cannot get a frame dropped. A photo that is not part of a burst is never dropped for its faces: there is nothing better to keep instead. |
| Composition | Accidental shots (the shutter pressed unintentionally: a blank surface, the ground, a hand over the lens, smeared motion). A local vision-language model (Qwen3-VL 4B) sees each photo, as it will look after its proposed rotation, and sorts it into accident, deliberate record (document, sign, receipt, object) or normal photo; a photo is left behind when the accident probability is 0.5 or more. It does not judge how well a deliberate photo is composed. |
| Receipts | Photos of receipts, bills, invoices and payment slips, taken for expense tracking and not wanted in a photo archive. The same model is asked a second yes/no question about each photo it took for a deliberate record; a photo is left behind when the answer is "yes" with probability 0.5 or more. |

## Prerequisites

- `exiftool`, Python 3.10+, an NVIDIA GPU (CPU works but is slow). With several GPUs the model
  work is split across all of them, one process per GPU; set `CUDA_VISIBLE_DEVICES` to restrict it.
- A virtual environment outside the repo:

```bash
python3 -m venv ~/.venvs/photo-cull
~/.venvs/photo-cull/bin/pip install torch torchvision transformers pillow pillow-heif opencv-python-headless numpy easyocr mediapipe pytest
```

The first run downloads the DINOv2 weights (about 350 MB) to the Hugging Face cache and the
EasyOCR models (about 100 MB) to `~/.EasyOCR`, three face models (about 40 MB) to
`~/.cache/photo-cull`, and the vision-language model (about 9 GB) to the Hugging Face cache. The
text and vision models share each GPU and together peaked at 13 GB of GPU memory; on a smaller card
run with `--no-composition --no-receipts`, or with `--no-text-direction`. Without `easyocr`
installed the text check is skipped and the run says so; without `mediapipe`, faces are not
checked and every frame of a burst is kept.

## Workflow

`SRC` is the photo folder, `WORK` a new scratch folder, `OUT` a new folder for the keepers. Put
`WORK` somewhere the user can open in a browser.

```bash
PC="$HOME/.venvs/photo-cull/bin/python scripts/photo_cull.py"
$PC run "<SRC>" "<WORK>"              # scan + analyze in one pass -> plan.json, review.html (about 22 minutes per 8,400 photos the first time)
```

`run` is `scan` and `analyze` overlapped: the CPU decodes photos (the scan) while the GPUs already
work on the thumbnails written so far, which takes well under the sum of the two. They also exist
as separate commands. Use `analyze` alone to try other thresholds: everything measured is cached
in `WORK`, so it then takes seconds.

```bash
$PC scan    "<SRC>" "<WORK>"          # metadata, thumbnails, sharpness, faces (CPU, all cores)
$PC analyze "<WORK>"                  # decisions -> plan.json, review.html (GPU)
```

**Stop here. The user reviews `<WORK>/review.html` in a browser.** It lists every screenshot, every
proposed rotation (shown as it will look afterwards), every blurry pick, every accidental shot,
every receipt and every burst with a frame dropped for its faces.

- Each section (Rotate, Facial, Composition, Receipts, Blurry, Screenshots) is a tab at the top; the last tab used is
  remembered.
- Under Facial, each row is one burst. A dropped frame carries a red label with the reason
  (`eyes closed` or `bad facial expression`); every card with people shows `faces=` and the
  average `smile=` (0 to 1).
- Under Composition, each photo carries the label `looks accidental` and its `accident=`
  probability. Under Receipts, each photo shows its `receipt=` probability.
- The **Exclude** button on a photo leaves it behind or brings it back. Clicking the photo does the
  same, except under "Rotate", where a click turns the proposed rotation off or on.
- The ↻ button turns any photo a quarter turn clockwise, to correct a proposed rotation that came
  out wrong; the photo is saved the way it is shown.
- The arrow in a photo's corner opens it at full resolution. In that viewer a click zooms to 100%
  (drag the photo to move around it, click again to zoom out),
  the up/down keys step through the same burst group while holding the zoom position (so frames
  can be compared pixel for pixel), `x` excludes or keeps, and `Esc` closes. The left/right keys
  walk every photo in the folder in capture order, including ones not under review, so the user
  can see what was shot before and after; up or down returns to the photo under review.
- A slider sets the preview size; previews then stretch so each row fills the window width.
- Changes are saved in the browser (localStorage, keyed by the page's path) as the user goes and
  restored when the page is reopened in the same browser; **Reset my changes** discards them.
  Re-running `analyze` keeps them for photos still on the page.

The user then presses **Download overrides.json**, even when nothing was changed.

```bash
$PC apply "<SRC>" "<WORK>" "<OUT>" --overrides "<overrides.json>"           # dry run: counts only
$PC apply "<SRC>" "<WORK>" "<OUT>" --overrides "<overrides.json>" --apply   # copy and verify
```

`apply --apply` refuses to run without `--overrides`. **Never write that file yourself** to get past
the check: it is the evidence that the user reviewed the plan. The file names the plan it was
reviewed against; after `analyze` or `run` is run again, `apply` refuses an older file, and the
user reopens the page (their changes are still there) and downloads it again. If the user wants different
thresholds instead of many manual flips, re-run `analyze` with new values (see Tuning) and have
them review again.

Report to the user: the counts `apply` prints, any `PROBLEMS`, and where `OUT` and the log are.

## Learning from the review

`apply --apply` also saves what the review said about each proposal, outside `WORK` so it outlives
the scratch folder: one file per source folder in `~/.local/share/photo-cull/feedback/` (or
`$PHOTO_CULL_FEEDBACK`, or `--feedback-dir`). Applying the same folder again replaces its file.

```bash
$PC feedback        # how often each kind of proposal was accepted, over every folder reviewed so far
```

Each file is JSON lines: a `run` line (source, thresholds used, counts), then one line per photo
that was on the review page with `category` (`screenshot`, `blurry`, `facial`, `composition`, `receipt`, `rotate`, or `none`
for a photo shown only as context), `proposed` and `final` (`keep`, `rotate`), `agreed`, and the
measurements the proposal rested on (`sharpness`, `rotate_conf`, `rotate_by`, `text_scores`,
`faces`, `smile`, `eyes_closed`, `accident`, `receipt`, `why`).

Use it when the user asks to make the skill more accurate, or when a category's accepted share is
low after a few folders:

- Compare the measurements of accepted and overridden proposals to move a threshold (for example
  the `sharpness` of blurry picks the user kept, against `--blur-below`).
- An overridden `none` record is something the tool missed and the user fixed by hand.
- The records are labelled examples (photo, proposal, what the user wanted) for training or
  evaluating a model later; the photos themselves stay in the source folder named in the `run` line.

It only knows about photos that were on the review page: a bad photo the tool never flagged and the
user never saw is not recorded. The files hold file names and paths, not pictures; keep them
private all the same.

## What `apply` guarantees

- Copies only; the source is read-only to this tool. It exits if the source changed since `scan`.
- Every file in the source is accounted for: kept, or left behind with a reason.
- A Live Photo's video and every Takeout JSON sidecar follow their photo. Other videos, unreadable
  images and unrecognised files are always kept.
- Unrotated copies are verified by SHA-256 against the source.
- Rotation changes only the orientation flag (one byte of a HEIC's `irot` box, or the EXIF
  `Orientation` tag of a JPEG). Each rotated copy is decoded and compared pixel for pixel with the
  rotated source. PNG and HEICs without an `irot` box are kept unrotated and reported.
  A rotated HEIC keeps its old EXIF `Orientation` tag: HEIF viewers follow `irot` and ignore that
  tag, but `exiftool -Orientation` will still print the old value (`-Rotation` shows the new one).
- It never overwrites a different file already in `OUT`; that is reported as a problem. Each file
  is copied under a temporary name and renamed once complete, so an interrupted run leaves no
  half-written photo; re-running resumes.
- Exit status is non-zero if any problem was found.

## Logs and outputs (all in `WORK`)

| File | Content |
|---|---|
| `operations.jsonl` | Audit trail, one JSON object per line, appended by every stage: `stage`, `event`, `ts` plus fields. `analyze`/`decision` has one line per image with `keep`, `reason`, `sharpness`, `rotate`, `rotate_by` (`scene` or `text`), `rotate_conf` (scene check), `text_scores`, `group`, `kept_instead`, and for burst frames with people `faces`, `smile`, `eyes_closed` (known people only); `accident` (probability that the shot was accidental) and `receipt`; a frame with `reason` `facial` also has `why` (`eyes closed` or `bad facial expression`) and `kept_instead` (the best frame of its burst). `apply` has one `kept` (with `verified`, `rotate`) or `left_behind` (with `reason`) line per source file, plus one `override` line per manual change. `scan` and `analyze` each end with a `timing` line: wall-clock seconds per step (steps of `analyze` overlap, so they add up to more than its `total`). The same review is also saved as feedback across folders, see "Learning from the review". |
| `scan.json` | Per-file metadata and measurements. |
| `plan.json` | Proposed decision per image and the thresholds used. |
| `review.html` | The review page (needs the `thumbs/` and `full/` folders beside it). |
| `text_scores.json` | Cache of the text check: per photo, how much text read cleanly after 0-3 clockwise quarter turns, or null when the photo shows almost no text. |
| `composition.json` | Cache of the accidental-shot and receipt checks: per photo, the rotation it was judged at and the accident / record / normal / receipt probabilities. |
| `face_scores.json` | Cache of the face check, filled by the scan for every photo taken within 30 seconds of another (only those can be in a burst): each face's position, width, eye-closure and smile scores, sharpness and recognition embedding. The embeddings are biometric data of the people in the photos: keep the file with the photos, do not share it. |
| `full/` | Full-resolution JPEG previews of the photos under review, because browsers cannot show HEIC. Roughly 3-4 MB per photo; skip with `--no-full-previews`. Safe to delete after `apply`. |
| `apply_report.json` | Final kept / left-behind lists and any problems. |

Useful queries:

```bash
jq -c 'select(.event=="decision" and .keep==false)' "<WORK>/operations.jsonl"   # every proposed reject
jq -c 'select(.stage=="apply" and .event=="left_behind")' "<WORK>/operations.jsonl"
jq -c 'select(.event=="override")' "<WORK>/operations.jsonl"                    # what the user changed
```

## Tuning

Flags of `analyze` and `run`; re-running `analyze` takes seconds because everything measured is cached in `WORK`.

| Flag | Default | Raise it to |
|---|---|---|
| `--blur-below` | 20 | reject more photos as blurry |
| `--rotate-confidence` | 0.95 | propose fewer rotations |
| `--accident-confidence` | 0.5 | flag fewer photos as accidental |
| `--receipt-confidence` | 0.5 | leave fewer photos behind as receipts |
| `--burst-seconds` | 30 | group shots taken further apart |
| `--burst-similarity` | 0.85 | require frames to be more alike before grouping |

`--text-languages en,no` sets the EasyOCR language codes of the text on signs (default `en`, which
also copes with most Latin-script languages for this purpose; add `ch_sim`, `ja` and so on for
other scripts). `--no-text-direction` turns the text check off. `--no-faces` skips the face check, so
nothing is dropped from a burst. `--no-composition` skips the accidental-shot check and `--no-receipts` keeps receipts;
`--composition-model` names another Hugging Face vision-language model to ask.

Calibration notes from one year of iPhone photos (about 8,000): at 0.85 sampled burst groups were all
true repeats, while below about 0.8 "different person, same spot" pairs start to appear. Rotation
proposals above 0.99 confidence were all correct; between 0.90 and 0.95 a few were wrong 180-degree
flips of signs photographed from above.
The text check, on the same folder: it read thumbnails only, added 69 rotations of sideways or upside-down signs and documents that the
scene check had missed, all correct on inspection, and cancelled one wrong scene rotation. Sideways
text in a photo the scene check is at least 0.9 sure is upright (lettering on an open car door, a
beer mat) is ignored.
The face check, on the same folder: of 678 bursts about 380 had faces. Four groups of linked faces were seen on 3 or more
days and so count as known: two hold nearly all the family's faces (several relatives merged into
one), one is a mix of non-faces (a pet, statues, paintings). Counting bursts instead of days had
also "learned" a bust photographed four times and a face on a television; a companion on a one-day
outing is a stranger either way. 17 frames in 7 bursts were dropped, nearly all for expression, all
sensible on inspection: a flat face among smiling frames,
a tongue stuck out, the first ten frames of a thirty-frame burst before one person started smiling.
The eye-closure score also rises for glasses, hat shadows and people looking down, so the user
should check each dropped frame. Before strangers were ignored, four more frames were dropped for
the faces of people in a crowd.
Faces the landmark model cannot read (profiles, pets, statues) are ignored: an earlier rule that
dropped frames for a "face turned away" was wrong more often than right.

The accidental-shot check, on the same folder. Three approaches were
compared. An aesthetics/quality scorer (Q-ReAlign Mini) and a composition-rating model
(PhotoFramer, far too slow besides) both ranked wanted photos at the bottom: fireworks, fog, a pet,
scans of old portraits, licence plates, tickets and documents are plain or badly composed and still
wanted. Asking a general vision-language model whether the photo was an accident found the real
throwaways. A yes/no question flagged 55 photos including sideways signs and documents; judging the
rotated preview and offering "deliberate record" as a third answer cut that to 24 with almost no
such false alarms (20 with the current build). Of those about 15 were clear throwaways (blank wall, featureless sand, water
and fog, a hand over the lens, smeared motion); the rest were arguable (a carpet stain photographed
on purpose). It misses some accidents with recognisable content (a trouser leg, a road from a car
window).

The receipt check, on the same folder: 36 photos. The 27 scored 1.00 were all shop, restaurant
and fuel receipts. The 9 scored below that were a mix: an invoice and a parking slip, but also
old handwritten documents photographed in a museum and a printed form. Tell the user to look at
the low-scoring end of the Receipts tab.

## Speed

Measured on 24 CPU threads and two 16 GB GPUs: 8,415 photos in 22 minutes with `run`. On a
1,000-photo sample `run` took 166 s where the same work done one step after another took 386 s.
What decides the time:

- **Decoding the photos** (CPU) is the scan's limit: about 1.3 CPU-seconds per 12-megapixel HEIC,
  so each photo is opened once and everything that needs full pixels (sharpness, thumbnail,
  faces) is taken from that one decode. More decoder threads per process do not help.
- **The vision-language model** (GPU) is the analysis's limit: about 0.15 s per photo per GPU. It
  starts on the first thumbnails while the scan is still running, judging photos as stored; the
  2% or so the plan turns are asked again at the end.
- The scan and the GPU work finish at about the same time, so speeding up only one of them gains
  little. Memory is not a limit (32 GB of RAM at peak).

Results do not depend on how the work is scheduled: on a 1,000-photo sample every decision of
`run` matched the one-step-at-a-time result. The tests hold this (`run` against `scan` then
`analyze`, several metadata readers against one, two GPUs against one). Every stage logs a
`timing` line to `operations.jsonl`; read it before changing anything for speed, and compare
decisions before and after.

## Limits

- One flat folder per run; subfolders are ignored. `WORK` must be outside `SRC`, and a scan
  empties `WORK/thumbs` and `WORK/full` (it refuses a folder that has those but is not its own).
- Blur detection is the weakest part. The sharpness measure cannot tell a blurred photo from a
  sharp photo of a scene with little detail (fireworks over dark water, open sky, underwater), so
  expect such scenes among the blurry picks and tell the user to check that section with care.
- People are learned from the burst photos of this one folder. A friend seen on only one or two
  days counts as a stranger and is not judged. If nobody is seen on 3 days (a small folder, a
  single outing), everyone is judged; the run prints which case applies. Linked faces are not
  always one person (family members can merge, and so can non-faces such as pets and statues),
  which does not matter for telling known from stranger.
- The accidental-shot check judges a 640-pixel preview and cannot know intent: a deliberate photo
  of a plain surface (sand patterns, a stain, a texture) looks like an accident to it.
- The receipt check takes other paper documents for receipts now and then, old handwritten
  ones in particular.
- The face check only compares frames of the same burst and only judges closed eyes and smiles.
  Framing and composition are not judged, a serious or candid expression counts as "no smile",
  and faces narrower than 3% of the photo or sideways in an unrotated photo are not read.
- Photos shot straight down with no readable text (food on a table) have no real "up"; rotation
  proposals for them are guesses the user should check. With text, the text decides.
- The text check needs a few readable words at thumbnail size, in a script the chosen
  `--text-languages` cover. Vertical writing and text running round a circle give no verdict.
- The rotation check assumes most photos in the folder are already upright and needs a few hundred
  photos to learn from.

## Tests

```bash
~/.venvs/photo-cull/bin/python -m pytest scripts/tests
```

## Place in the photo workflow

Runs after `dedup-copy` and before `organize-photos-and-videos-by-day`: use this skill's `OUT` as
that skill's `source_root`. See `PHOTO_VIDEO_WORKFLOW.md`.
