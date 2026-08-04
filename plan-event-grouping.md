# Plan: pulling events out of a chaos folder

Status: planned, not implemented. Agreed 2026-08-02.

## The problem

A phone dump folder holds hundreds of unrelated pictures plus the occasional run
that clearly belongs together — one birthday, one hike, one museum visit. Those
runs should live in their own folder, but `namer.py` can only rename pictures
where they lie. So either the run gets forgotten and stays in the chaos, or it
has to be fished out by hand in Explorer afterwards, which is when the forgetting
usually happens.

## The idea

Events in a phone dump are almost always a *contiguous run in time*. That makes
"where does this event start and end" a far cheaper question than "which pictures
belong together", so the feature is built in three layers, each useful on its own:

| Layer  | What you do                                             | Cost per event   |
|--------|---------------------------------------------------------|------------------|
| Basket | `Insert` toggles a picture into a pile, move the pile    | 1 key / picture  |
| Grab   | `alt+g` takes everything within a few hours of this one  | 1 key / event    |
| Split  | `alt+b` marks a boundary, the folder splits at the marks | 1 key / event    |

Only the basket is in scope for v1: it needs the least machinery and it is the
one that also covers the pictures no time window will ever group correctly.

A basket without a name goes to `_sort me in/`, so a picture can always be set
aside without deciding anything about it first.

## Decisions

- **Sorted by date taken**, not by filename. Every grouping idea above leans on
  the order being chronological, and a folder fed from several devices is not.
  The price is that every date has to be resolved before the order is known.
- **Only the image file moves.** Same-stem siblings (`.AAE`, live photo `.MOV`)
  stay behind. Dragging them along was considered and rejected: a move that
  touches files you cannot see is worse than an orphan you can.
- **Moving never renames.** Pull the event out first, then run the tool again on
  the new folder to name what is in it. Otherwise the text field would mean two
  different things depending on which key ends the edit.
- **The basket lives in memory only.** Quitting drops it; the moves are the
  persistence.

## v1

### Startup

`get_date_taken` reopens the file on every call, and both `_render_footer` and
`build_new_filename` call it per picture. Sorting by date needs the date of every
picture up front, so it gets a cache:

- `self.date_cache: dict[Path, tuple[datetime, str]]` in front of
  `get_date_taken`.
- `_collect_images` sorts by resolved date, falling back to the filename where
  two pictures share a date.
- The scan runs on a worker thread that posts results back through
  `window.after`, so the first picture is interactive immediately. The footer
  shows `Reading dates... 340 / 1200`; the list settles into its final order once
  the scan completes.

### Marking

- `Insert` — toggle the current picture in the basket **and advance**.
- `shift+Insert` — toggle without advancing.
- `alt+c` — clear the basket.

`Insert` never produces a character, so unlike `left`, `right` and `del` it needs
no "only once the text field has nothing left to edit" guard.

### Moving

`ctrl+return` puts the footer into target mode:

- the entry changes colour and the label above it reads `Move 7 pictures to →`
- prefilled with the folder used last, `tab` completes over the subfolders that
  already exist in the working directory
- `return` commits, `esc` cancels back to naming mode — `esc` only quits the app
  outside this mode
- an empty target means `_sort me in/`

The move itself creates the folder if needed and renames each picture into it,
reusing the `-1`, `-2` collision loop. An existing, non-empty target folder is
fine: that is the "this event was already half extracted" repair case.

### Undo

`ctrl+z` moves the last batch back where it came from and removes the folder
again if nothing else ended up in it. Tk's `Entry` has no undo of its own, so the
binding is free.

### What it looks like

- A marked current picture gets a coloured frame and a corner ribbon, drawn in
  `_paste_picture`.
- Marked tiles in the NEXT UP panel get a check badge beside their position
  badge.
- A second card below NEXT UP, header `BASKET (7)`, showing the most recent
  thumbnails. `_build_preview_panel` becomes
  `_build_tile_panel(header=, paths=, highlight=)`, and
  `_fitting_preview_count` divides the available height between the two cards.
- A footer line `Basket: 7 · ctrl+return move · alt+c clear`, only while the
  basket holds something.

### Refactors this needs first

- `_forget(image_path)` — the cache, draft and index bookkeeping that
  `_trash_current` already does. A move needs exactly the same thing.
- `_unique_target(target)` — the collision loop currently living inside
  `rename_image`.

Both are pulled out before the feature is written, so the move path shares the
behaviour instead of reimplementing it.

### Untouched on purpose

`_collect_images` filters on `is_file()`, so a freshly created event folder never
turns up in the picture list. Moved pictures simply disappear from the run and
nothing has to be told about it.

## Later

- **v2** — `alt+g` grabs every picture within `EVENT_GAP` (3h, configurable) of
  the current one, walking outward in time, and reports what it took. Plus a
  divider between two NEXT UP tiles whenever the gap between them exceeds
  `EVENT_GAP`: it makes the end of an event visible *before* walking past it,
  which is the exact moment the extraction gets forgotten today.
- **v3** — `alt+b` marks an event boundary, and one command splits the whole
  folder at every mark. `F1`–`F4` move the basket into one of the recently used
  folders without typing.

## Keys, all together

| Key            | v1                                        |
|----------------|-------------------------------------------|
| `Insert`       | toggle in basket, advance                 |
| `shift+Insert` | toggle in basket, stay                    |
| `alt+c`        | clear basket                              |
| `ctrl+return`  | move basket → target mode                 |
| `return`       | commit target (target mode only)          |
| `tab`          | complete folder name (target mode only)   |
| `esc`          | cancel target mode, otherwise quit        |
| `ctrl+z`       | undo last move                            |

Existing bindings — `ctrl+s`, the arrows, `del`, `esc` — keep working as they do
now.
