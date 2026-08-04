import argparse
import math
import os
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
from pillow_heif import register_heif_opener

register_heif_opener()


@dataclass
class MediaItem:
    """One picture or video, with the moment it was taken already resolved."""

    path: Path
    taken: datetime
    source: str
    # Whether ``taken`` carries a real time of day. A date pulled out of a name
    # like "Foo (13.04.26)" does not, so it lands at midnight and clusters as
    # one lump with everything else from that day.
    is_exact: bool
    is_video: bool


class EventGrouper:
    """Find runs of pictures that belong together and move them into subfolders.

    An event - a party, a stroll, a vacation - is almost always a contiguous run
    in time. So the folder is clustered by timestamp, every candidate run is
    shown as a contact sheet, and one keypress moves it into
    ``<name> (dd.mm.yy - dd.mm.yy)/`` next to the pictures it came from.
    """

    DIR_PATH = r"D:\Dropbox\Fotos\neu + unsortiert\C Chaos\Handy-Fotos 2026"

    IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".heic", ".avif", ".webp", ".gif")
    VIDEO_TYPES = (".mp4", ".mov", ".avi", ".m4v", ".3gp")

    # -- clustering --------------------------------------------------------
    # A gap this big ends a session: you put the camera away
    SESSION_GAP = timedelta(hours=3)
    # Two sessions closer than this can still be one event - this is the night
    # in the middle of a vacation. Generous, because the real test is the one
    # below: an event covers consecutive days, and this only guards against a
    # morning and a late night of the same day being called one thing.
    MERGE_GAP = timedelta(hours=20)
    # ... but only if both sides are substantial. Without this one stray photo
    # the morning after would glue two unrelated days together.
    MIN_MERGE_SESSION = 4
    # A run smaller than this is a single or a random photo, never an event
    MIN_EVENT_SIZE = 5

    PLACEHOLDER_NAME = "Event"

    # Timestamps written by ``renamer.py`` / ``exif.py``: "2026-04-13 14.30.22".
    # The time is optional, but without it the picture only clusters by day.
    PATTERN_TIMESTAMP = (
        r"^(\d{4})-(\d{2})-(\d{2})(?:[\s_](\d{2})[.\-](\d{2})[.\-](\d{2}))?"
    )
    # What phones write themselves: "IMG_20140503_143022", "VID_20140503_143022".
    # For a video this is usually the only precise source there is.
    PATTERN_COMPACT = r"(?<!\d)(\d{4})(\d{2})(\d{2})[_\-]?(\d{2})(\d{2})(\d{2})(?!\d)"
    # Names ``namer.py`` has written before: "Office at night (13.04.26)"
    PATTERN_NAMED = r"^(.*)\s\((\d{2})\.(\d{2})\.(\d{2})\)$"

    # "DateTimeOriginal" lives in the Exif sub-IFD, "DateTime" in the main one
    EXIF_IFD = 0x8769
    EXIF_DATE_TIME_ORIGINAL = 36867
    EXIF_DATE_TIME = 306

    FORBIDDEN_CHARS = r'[\\/:*?"<>|]'

    BACKGROUND_COLOR = "#1e1e1e"
    TEXT_COLOR = "#dddddd"
    HINT_COLOR = "#888888"
    PREVIEW_COLOR = "#7ec699"
    ERROR_COLOR = "#e06c75"
    WARN_COLOR = "#d19a66"

    PANEL_FILL = (38, 38, 38, 255)
    PANEL_OUTLINE = (255, 255, 255, 20)
    TILE_FILL = (0, 0, 0, 120)
    TILE_OUTLINE = (255, 255, 255, 40)

    # -- contact sheet -----------------------------------------------------
    # More tiles than this and none of them is big enough to recognise, so a
    # bigger group is sampled evenly across its whole run instead of truncated
    MAX_SHEET_TILES = 40
    SHEET_GAP = 8
    MARGIN = 14
    TIMELINE_HEIGHT = 96
    # Thumbnails are cached at one generous size and scaled down per tile, so
    # resizing the window never decodes anything twice
    THUMB_SIZE = (256, 256)
    THUMB_CACHE_SIZE = 240
    THUMB_POLL_MS = 80
    RESIZE_DEBOUNCE_MS = 100

    def __init__(
        self,
        *,
        dir_path: str,
        session_gap: Optional[timedelta] = None,
        merge_gap: Optional[timedelta] = None,
        min_event_size: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.dir_path = Path(dir_path)
        self.session_gap = session_gap or self.SESSION_GAP
        self.merge_gap = merge_gap or self.MERGE_GAP
        self.min_event_size = min_event_size or self.MIN_EVENT_SIZE

        self.items: list[MediaItem] = []
        self.sessions: list[list[MediaItem]] = []
        # The proposal on screen is ``sessions[start:end]``; the cursor is where
        # the search for the next one begins
        self.cursor = 0
        self.start: Optional[int] = None
        self.end: Optional[int] = None
        self.group_number = 0
        self.total_estimate = 0
        self.moved_groups = 0
        self.moved_files = 0
        # (item, old path, new path) of the last batch, for ctrl+z
        self.last_move: Optional[dict] = None

        self.thumb_cache: dict[Path, Optional[Image.Image]] = {}
        self.thumb_requested: set[Path] = set()
        self.thumb_queue: queue.Queue = queue.Queue()
        self.thumb_done: queue.Queue = queue.Queue()
        self.font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}

        self.rendered_size: tuple[int, int] = (0, 0)
        self.rendered_key: Optional[tuple] = None
        self.photo_image: Optional[ImageTk.PhotoImage] = None
        self.resize_job: Optional[str] = None
        # Field content at the moment a message was put on the preview line, so
        # a stray key release cannot wipe the confirmation a shortcut wrote
        self.status_entry_text: Optional[str] = None

    # -- collecting --------------------------------------------------------

    def _collect_media(self) -> list[MediaItem]:
        items = []
        for element in os.scandir(self.dir_path):
            if not element.is_file():
                continue

            path = Path(element.path)
            suffix = path.suffix.lower()
            if suffix in self.IMAGE_TYPES:
                is_video = False
            elif suffix in self.VIDEO_TYPES:
                is_video = True
            else:
                continue

            taken, source, is_exact = self.get_date_taken(
                path=path, is_video=is_video
            )
            items.append(
                MediaItem(
                    path=path,
                    taken=taken,
                    source=source,
                    is_exact=is_exact,
                    is_video=is_video,
                )
            )

        return sorted(items, key=lambda item: (item.taken, item.path.name.lower()))

    # -- date resolution ---------------------------------------------------

    def _date_from_exif(self, *, path: Path) -> Optional[datetime]:
        try:
            with Image.open(path) as im:
                exif = im.getexif()
                date_taken = exif.get_ifd(self.EXIF_IFD).get(
                    self.EXIF_DATE_TIME_ORIGINAL
                ) or exif.get(self.EXIF_DATE_TIME)
        # Broken or half written EXIF blocks raise all sorts of things, and a
        # missing date is not a reason to give up on the file
        except (OSError, SyntaxError, ValueError, TypeError, KeyError):
            return None

        if not date_taken:
            return None

        try:
            return datetime.strptime(str(date_taken), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return None

    def _date_from_filename(self, *, path: Path) -> Optional[tuple[datetime, bool]]:
        matches = re.search(self.PATTERN_TIMESTAMP, path.name)
        if not matches:
            return None

        has_time = matches[4] is not None
        try:
            return (
                datetime(
                    int(matches[1]),
                    int(matches[2]),
                    int(matches[3]),
                    int(matches[4]) if has_time else 0,
                    int(matches[5]) if has_time else 0,
                    int(matches[6]) if has_time else 0,
                ),
                has_time,
            )
        except ValueError:
            return None

    def _date_from_compact_name(self, *, path: Path) -> Optional[datetime]:
        matches = re.search(self.PATTERN_COMPACT, path.stem)
        if not matches:
            return None

        try:
            found = datetime(*(int(group) for group in matches.groups()))
        except ValueError:
            return None

        # Six digits in a row are easy to come by, so only believe a year that
        # a camera could plausibly have written
        if not 1990 <= found.year <= 2100:
            return None
        return found

    def _date_from_own_name(self, *, path: Path) -> Optional[datetime]:
        matches = re.search(self.PATTERN_NAMED, path.stem)
        if not matches:
            return None

        try:
            return datetime.strptime(
                f"{matches[2]}.{matches[3]}.{matches[4]}", "%d.%m.%y"
            )
        except ValueError:
            return None

    def get_date_taken(self, *, path: Path, is_video: bool) -> tuple[datetime, str, bool]:
        """When the file was taken, where that came from, and whether it has a time."""
        if not is_video:
            date_taken = self._date_from_exif(path=path)
            if date_taken:
                return date_taken, "EXIF", True

        from_filename = self._date_from_filename(path=path)
        if from_filename:
            date_taken, has_time = from_filename
            return date_taken, "filename", has_time

        date_taken = self._date_from_compact_name(path=path)
        if date_taken:
            return date_taken, "filename", True

        # A picture named earlier already carries the date worked out back then
        date_taken = self._date_from_own_name(path=path)
        if date_taken:
            return date_taken, "existing name", False

        return datetime.fromtimestamp(path.stat().st_mtime), "file date", True

    # -- clustering --------------------------------------------------------

    def _build_sessions(self, *, items: list[MediaItem]) -> list[list[MediaItem]]:
        """Split the run wherever the camera was away for longer than the gap."""
        sessions: list[list[MediaItem]] = []
        for item in items:
            if sessions and item.taken - sessions[-1][-1].taken <= self.session_gap:
                sessions[-1].append(item)
            else:
                sessions.append([item])
        return sessions

    def _event_end(self, *, start: int) -> int:
        """One past the last session belonging to the event starting at ``start``.

        Two sessions are the same event when they fall on the same or on
        consecutive days *and* both sides hold enough files to be a day of
        something. Counting calendar days rather than hours is what makes a
        vacation arrive as one group: the night in between runs anywhere from
        eleven to nineteen hours depending on when dinner was, and no hour
        threshold separates that from a day with nothing in it at all.

        The size condition is the other half. Without it a single photo the
        morning after a party would glue two unrelated days together.
        """
        end = start + 1
        while end < len(self.sessions):
            previous, following = self.sessions[end - 1], self.sessions[end]
            if (following[0].taken.date() - previous[-1].taken.date()).days > 1:
                break
            if following[0].taken - previous[-1].taken > self.merge_gap:
                break
            if (
                len(previous) < self.MIN_MERGE_SESSION
                or len(following) < self.MIN_MERGE_SESSION
            ):
                break
            end += 1
        return end

    def _next_proposal(self, *, cursor: int) -> Optional[tuple[int, int]]:
        """The next run big enough to be worth looking at, or ``None``."""
        index = cursor
        while index < len(self.sessions):
            end = self._event_end(start=index)
            if sum(len(session) for session in self.sessions[index:end]) >= (
                self.min_event_size
            ):
                return index, end
            # Nothing inside a run this small can qualify either, so step over
            # the whole of it
            index = end
        return None

    def _all_proposals(self) -> list[tuple[int, int]]:
        """Every group you would see if you accepted each one as it came up."""
        proposals = []
        cursor = 0
        while True:
            proposal = self._next_proposal(cursor=cursor)
            if proposal is None:
                return proposals
            proposals.append(proposal)
            cursor = proposal[1]

    @property
    def current_group(self) -> list[MediaItem]:
        return [
            item for session in self.sessions[self.start : self.end] for item in session
        ]

    # -- folder naming -----------------------------------------------------

    def clean_name(self, *, name: str) -> str:
        cleaned = re.sub(self.FORBIDDEN_CHARS, "", name)
        return re.sub(r"\s+", " ", cleaned).strip().rstrip(".")

    def date_suffix(self, *, items: list[MediaItem]) -> str:
        """``(13.04.26)`` for one day, ``(13.04.26 - 16.04.26)`` for a run."""
        first, last = items[0].taken, items[-1].taken
        if first.date() == last.date():
            return f"({first.strftime('%d.%m.%y')})"
        return f"({first.strftime('%d.%m.%y')} - {last.strftime('%d.%m.%y')})"

    def build_folder_name(self, *, name: str, items: list[MediaItem]) -> str:
        return f"{self.clean_name(name=name)} {self.date_suffix(items=items)}"

    # -- moving ------------------------------------------------------------

    @staticmethod
    def _move_to(*, source: Path, target: Path) -> Path:
        """Move the file, dodging collisions with a ``-1``, ``-2``, ... suffix."""
        stem, suffix = target.stem, target.suffix
        failure_counter = 0

        while True:
            try:
                os.rename(source, target)
            except FileExistsError:
                failure_counter += 1
                target = target.with_name(f"{stem}-{failure_counter}{suffix}")
            else:
                return target

    def move_group(
        self, *, items: list[MediaItem], folder: Path
    ) -> tuple[list[tuple[MediaItem, Path, Path]], list[str]]:
        """Move every file into ``folder``, reporting the ones that would not go."""
        moves: list[tuple[MediaItem, Path, Path]] = []
        failures: list[str] = []

        for item in items:
            old_path = item.path
            try:
                new_path = self._move_to(source=old_path, target=folder / old_path.name)
            except OSError as error:
                failures.append(f"{old_path.name} ({error.strerror or error})")
            else:
                moves.append((item, old_path, new_path))
                item.path = new_path

        return moves, failures

    # -- thumbnails --------------------------------------------------------

    def _thumb_worker(self) -> None:
        """Decode previews off the main thread, so the window never freezes."""
        while True:
            path = self.thumb_queue.get()
            try:
                with Image.open(path) as im:
                    # Lets the JPEG decoder skip straight to a small size
                    im.draft("RGB", self.THUMB_SIZE)
                    thumb = ImageOps.exif_transpose(im)
                    thumb.thumbnail(self.THUMB_SIZE, Image.LANCZOS)
                    thumb = thumb.convert("RGB")
            except (OSError, SyntaxError, ValueError):
                thumb = None
            self.thumb_done.put((path, thumb))

    def _request_thumbs(self, *, items: list[MediaItem]) -> None:
        for item in items:
            if item.is_video or item.path in self.thumb_cache:
                continue
            if item.path in self.thumb_requested:
                continue
            self.thumb_requested.add(item.path)
            self.thumb_queue.put(item.path)

    def _drain_thumbs(self) -> None:
        arrived = False
        while True:
            try:
                path, thumb = self.thumb_done.get_nowait()
            except queue.Empty:
                break
            self.thumb_cache[path] = thumb
            self.thumb_requested.discard(path)
            arrived = True

        if arrived:
            while len(self.thumb_cache) > self.THUMB_CACHE_SIZE:
                self.thumb_cache.pop(next(iter(self.thumb_cache)))
            # The sheet is missing tiles it can now fill in
            self.rendered_key = None
            self._render()

        self.window.after(self.THUMB_POLL_MS, self._drain_thumbs)

    # -- drawing helpers ---------------------------------------------------

    @staticmethod
    def _count(*, number: int, noun: str) -> str:
        return f"{number} {noun}" if number == 1 else f"{number} {noun}s"

    @staticmethod
    def _rgb(*, color: str) -> tuple[int, int, int]:
        """``"#7ec699"`` to ``(126, 198, 153)``, ready for a PIL fill."""
        return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))

    def _font(self, *, size: int, bold: bool = False) -> ImageFont.ImageFont:
        key = (size, bold)
        if key not in self.font_cache:
            try:
                name = "segoeuib.ttf" if bold else "segoeui.ttf"
                self.font_cache[key] = ImageFont.truetype(name, size)
            except OSError:
                self.font_cache[key] = ImageFont.load_default()
        return self.font_cache[key]

    @staticmethod
    def _draw_centered(*, draw, box, text, font, fill) -> None:
        """Centre ``text`` in ``box``; works with bitmap fonts, too."""
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        x = box[0] + (box[2] - box[0] - (right - left)) // 2 - left
        y = box[1] + (box[3] - box[1] - (bottom - top)) // 2 - top
        draw.text((x, y), text, font=font, fill=fill)

    # -- contact sheet -----------------------------------------------------

    def _sheet_items(self, *, items: list[MediaItem]) -> list[MediaItem]:
        """At most ``MAX_SHEET_TILES``, spread evenly over the whole run.

        Sampling beats truncating: the point of the sheet is to judge whether
        the *end* of the group still belongs to it, and the first forty tiles
        of a hundred-shot vacation never show that.
        """
        if len(items) <= self.MAX_SHEET_TILES:
            return items
        step = len(items) / self.MAX_SHEET_TILES
        return [items[int(index * step)] for index in range(self.MAX_SHEET_TILES)]

    def _grid(
        self, *, width: int, height: int, count: int
    ) -> tuple[int, int, int]:
        """Columns and tile size that make the tiles as big as the area allows."""
        best = (0.0, 1, 1, 1)
        for columns in range(1, count + 1):
            rows = math.ceil(count / columns)
            tile_width = (width - self.SHEET_GAP * (columns + 1)) / columns
            tile_height = tile_width * 3 / 4
            if tile_height * rows + self.SHEET_GAP * (rows + 1) > height:
                # Too tall this way round, so the height is what limits it
                tile_height = (height - self.SHEET_GAP * (rows + 1)) / rows
                tile_width = tile_height * 4 / 3
            if tile_width < 24 or tile_height < 18:
                continue
            area = tile_width * tile_height
            if area > best[0]:
                best = (area, columns, int(tile_width), int(tile_height))

        _, columns, tile_width, tile_height = best
        return columns, tile_width, tile_height

    def _paste_sheet(self, *, frame: Image.Image, items: list[MediaItem], height: int):
        area_width = frame.width - 2 * self.MARGIN
        shown = self._sheet_items(items=items)
        self._request_thumbs(items=shown)

        columns, tile_width, tile_height = self._grid(
            width=area_width, height=height, count=len(shown)
        )
        rows = math.ceil(len(shown) / columns)
        sheet_width = columns * tile_width + (columns - 1) * self.SHEET_GAP
        sheet_height = rows * tile_height + (rows - 1) * self.SHEET_GAP
        origin_x = self.MARGIN + (area_width - sheet_width) // 2
        origin_y = self.MARGIN + (height - sheet_height) // 2

        sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(shown):
            left = (index % columns) * (tile_width + self.SHEET_GAP)
            top = (index // columns) * (tile_height + self.SHEET_GAP)
            self._draw_tile(
                sheet=sheet,
                draw=draw,
                box=(left, top, left + tile_width, top + tile_height),
                item=item,
            )

        frame.paste(sheet, (origin_x, origin_y), sheet)

    def _draw_tile(self, *, sheet: Image.Image, draw, box, item: MediaItem) -> None:
        draw.rounded_rectangle(box, radius=6, fill=self.TILE_FILL)

        if item.is_video:
            # Drawn rather than typed: Segoe UI has no glyph for U+25B6 and
            # falls back to a tofu box
            middle_x, middle_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            size = max(7, (box[3] - box[1]) // 7)
            draw.polygon(
                [
                    (middle_x - size * 0.6, middle_y - size),
                    (middle_x - size * 0.6, middle_y + size),
                    (middle_x + size, middle_y),
                ],
                fill=self._rgb(color=self.HINT_COLOR) + (255,),
            )
            draw.text(
                (box[0] + 6, box[3] - 18),
                item.path.suffix.lower().lstrip("."),
                font=self._font(size=9, bold=True),
                fill=self._rgb(color=self.HINT_COLOR) + (220,),
            )
        elif item.path in self.thumb_cache:
            thumb = self.thumb_cache[item.path]
            if thumb is None:
                # Cached as unreadable - the file stays in the group either way
                self._draw_centered(
                    draw=draw,
                    box=box,
                    text="?",
                    font=self._font(size=16),
                    fill=self._rgb(color=self.ERROR_COLOR) + (220,),
                )
            else:
                inner = thumb.copy()
                inner.thumbnail(
                    (box[2] - box[0] - 6, box[3] - box[1] - 6), Image.LANCZOS
                )
                sheet.paste(
                    inner,
                    (
                        box[0] + (box[2] - box[0] - inner.width) // 2,
                        box[1] + (box[3] - box[1] - inner.height) // 2,
                    ),
                )
        # Anything else is still in the decoding queue and stays an empty tile

        draw.rounded_rectangle(box, radius=6, outline=self.TILE_OUTLINE, width=1)

    # -- timeline ----------------------------------------------------------

    def _buckets(self, *, items: list[MediaItem]) -> list[tuple[str, int]]:
        """One bar per day, or per hour when the whole group is a single day."""
        if items[0].taken.date() != items[-1].taken.date():
            per_day: dict[str, int] = {}
            for item in items:
                key = item.taken.strftime("%a %d.%m")
                per_day[key] = per_day.get(key, 0) + 1
            return list(per_day.items())

        first, last = items[0].taken.hour, items[-1].taken.hour
        per_hour = {hour: 0 for hour in range(first, last + 1)}
        for item in items:
            per_hour[item.taken.hour] += 1
        return [(f"{hour:02d}h", count) for hour, count in per_hour.items()]

    def _paste_timeline(self, *, frame: Image.Image, items: list[MediaItem], top: int):
        width = frame.width - 2 * self.MARGIN
        height = self.TIMELINE_HEIGHT
        card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=12,
            fill=self.PANEL_FILL,
            outline=self.PANEL_OUTLINE,
        )

        buckets = self._buckets(items=items)
        padding = 14
        label_height, count_height = 14, 13
        baseline = height - padding - label_height
        span = baseline - (padding + count_height)
        slot = (width - 2 * padding) / len(buckets)
        peak = max(count for _, count in buckets)
        accent = self._rgb(color=self.PREVIEW_COLOR)
        # Below this the labels would run into each other, so only bars are left
        labelled = slot >= 34

        for index, (label, count) in enumerate(buckets):
            left = padding + index * slot
            bar_height = max(2, round(span * count / peak))
            draw.rectangle(
                (
                    round(left + slot * 0.14),
                    baseline - bar_height,
                    round(left + slot * 0.86),
                    baseline,
                ),
                fill=accent + (210,),
            )
            if not labelled:
                continue
            self._draw_centered(
                draw=draw,
                box=(left, baseline - bar_height - count_height, left + slot, baseline - bar_height),
                text=str(count),
                font=self._font(size=9, bold=True),
                fill=accent + (255,),
            )
            self._draw_centered(
                draw=draw,
                box=(left, baseline, left + slot, baseline + label_height),
                text=label,
                font=self._font(size=9),
                fill=self._rgb(color=self.HINT_COLOR) + (255,),
            )

        frame.paste(card, (self.MARGIN, top), card)

    # -- ui ----------------------------------------------------------------

    def _build_ui(self) -> None:
        self.window = tk.Tk()
        self.window.title(f"Event Grouper - {self.dir_path}")
        self.window.geometry("1200x900")
        self.window.configure(bg=self.BACKGROUND_COLOR)

        # A Canvas keeps the size it is given and ignores what is drawn on it.
        # A Label would request the size of its image instead, which makes pack
        # hand out ever more space on every render.
        self.canvas = tk.Canvas(
            self.window,
            bg=self.BACKGROUND_COLOR,
            width=1,
            height=1,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        footer = tk.Frame(self.window, bg=self.BACKGROUND_COLOR, padx=16, pady=10)
        footer.pack(fill=tk.X)

        self.info_label = tk.Label(
            footer,
            anchor="w",
            bg=self.BACKGROUND_COLOR,
            fg=self.HINT_COLOR,
            font=("Segoe UI", 9),
        )
        self.info_label.pack(fill=tk.X)

        self.entry = tk.Entry(
            footer,
            bg="#2d2d2d",
            fg=self.TEXT_COLOR,
            insertbackground=self.TEXT_COLOR,
            relief=tk.FLAT,
            font=("Segoe UI", 14),
        )
        self.entry.pack(fill=tk.X, ipady=6, pady=(6, 6))

        self.preview_label = tk.Label(
            footer,
            anchor="w",
            bg=self.BACKGROUND_COLOR,
            fg=self.PREVIEW_COLOR,
            font=("Segoe UI", 10),
        )
        self.preview_label.pack(fill=tk.X)

        tk.Label(
            footer,
            anchor="w",
            text=(
                "enter move   ctrl+n skip   "
                "alt+\u2190/\u2192 shrink/grow the group   ctrl+z undo   esc quit"
            ),
            bg=self.BACKGROUND_COLOR,
            fg=self.HINT_COLOR,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(6, 0))

        self.entry.bind("<KeyRelease>", lambda event: self._update_preview())
        self.window.bind("<Return>", self._on_accept)
        self.window.bind("<KP_Enter>", self._on_accept)
        self.window.bind("<Control-n>", self._on_skip)
        self.window.bind("<Control-N>", self._on_skip)
        self.window.bind("<Alt-Right>", self._on_grow)
        self.window.bind("<Alt-Left>", self._on_shrink)
        self.window.bind("<Control-z>", self._on_undo)
        self.window.bind("<Control-Z>", self._on_undo)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.canvas.bind("<Configure>", self._on_resize)

    def _render(self) -> None:
        self.resize_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return

        key = (width, height, self.start, self.end)
        # The very same thing is already on screen
        if key == self.rendered_key:
            return

        frame = Image.new("RGB", (width, height), self.BACKGROUND_COLOR)
        if self.start is None:
            self._draw_centered(
                draw=ImageDraw.Draw(frame),
                box=(0, 0, width, height),
                text="No groups left in this folder",
                font=self._font(size=16),
                fill=self._rgb(color=self.HINT_COLOR),
            )
        else:
            items = self.current_group
            timeline_top = height - self.TIMELINE_HEIGHT - self.MARGIN
            sheet_height = timeline_top - 2 * self.MARGIN
            if sheet_height > 60:
                self._paste_sheet(frame=frame, items=items, height=sheet_height)
                self._paste_timeline(frame=frame, items=items, top=timeline_top)
            else:
                # Window squashed flat - the sheet is the part worth keeping
                self._paste_sheet(frame=frame, items=items, height=height - 2 * self.MARGIN)

        self.photo_image = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image)
        self.rendered_size = (width, height)
        self.rendered_key = key

    def _render_footer(self) -> None:
        items = self.current_group
        photos = sum(1 for item in items if not item.is_video)
        videos = len(items) - photos
        sessions = "/".join(
            str(len(session)) for session in self.sessions[self.start : self.end]
        )
        days = (items[-1].taken.date() - items[0].taken.date()).days + 1

        parts = [
            f"Group {self.group_number} of ~{self.total_estimate}",
            f"{self._count(number=len(items), noun='file')} "
            f"({self._count(number=photos, noun='photo')}, "
            f"{self._count(number=videos, noun='video')})"
            if videos
            else self._count(number=photos, noun="photo"),
            self.date_suffix(items=items).strip("()"),
            f"{days} days" if days > 1 else items[0].taken.strftime("%H:%M")
            + " - "
            + items[-1].taken.strftime("%H:%M"),
            f"sessions {sessions}",
        ]
        if len(items) > self.MAX_SHEET_TILES:
            parts.append(f"showing {self.MAX_SHEET_TILES}")
        vague = sum(1 for item in items if not item.is_exact)
        if vague:
            parts.append(f"\u26a0 {vague} without a time of day")

        self.info_label.configure(text="   \u00b7   ".join(parts))
        self._update_preview()

    def _set_status(self, *, text: str, color: str) -> None:
        """Put a message on the preview line and let it stand until you type."""
        self.preview_label.configure(text=text, fg=color)
        self.status_entry_text = self.entry.get()

    def _update_preview(self) -> None:
        if self.status_entry_text is not None:
            # Nothing was typed since the message went up, so it stays
            if self.entry.get() == self.status_entry_text:
                return
            self.status_entry_text = None

        if self.start is None:
            return

        name = self.clean_name(name=self.entry.get())
        if not name:
            self.preview_label.configure(
                text="Type a folder name, then press enter", fg=self.HINT_COLOR
            )
            return

        folder = self.build_folder_name(name=name, items=self.current_group)
        self.preview_label.configure(text=f"\u2192 {folder}\\", fg=self.PREVIEW_COLOR)

    def _advance(self) -> None:
        """Show the next candidate group, or the closing summary."""
        self.status_entry_text = None
        proposal = self._next_proposal(cursor=self.cursor)
        if proposal is None:
            self.start = self.end = None
            self._show_done()
            return

        self.start, self.end = proposal
        self.group_number += 1
        self._prefill_entry(name=self.PLACEHOLDER_NAME)
        self._refresh()

    def _prefill_entry(self, *, name: str) -> None:
        self.entry.delete(0, tk.END)
        self.entry.insert(0, name)
        # Selected, so typing a real name replaces the placeholder outright
        self.entry.select_range(0, tk.END)
        self.entry.icursor(tk.END)
        self.entry.focus_set()

    def _refresh(self) -> None:
        self._render()
        self._render_footer()

    def _show_done(self) -> None:
        left = len(self.items) - self.moved_files
        self.entry.delete(0, tk.END)
        self.entry.configure(state=tk.DISABLED)
        self.info_label.configure(
            text=(
                f"Done   \u00b7   "
                f"{self._count(number=self.moved_groups, noun='group')} moved, "
                f"{self._count(number=self.moved_files, noun='file')}   \u00b7   "
                f"{self._count(number=left, noun='file')} left as singles "
                f"or random shots"
            )
        )
        self._render()
        self._set_status(text="Nothing left to group here", color=self.HINT_COLOR)

    # -- event handlers ----------------------------------------------------

    def _on_resize(self, event) -> None:
        if (event.width, event.height) == self.rendered_size:
            return
        # Redraw once the dragging stops, not on every pixel of the way
        if self.resize_job is not None:
            self.window.after_cancel(self.resize_job)
        self.resize_job = self.window.after(self.RESIZE_DEBOUNCE_MS, self._render)

    def _on_grow(self, event):
        if self.start is None:
            return "break"
        if self.end >= len(self.sessions):
            self._set_status(
                text="No session left to add - this is the end of the folder",
                color=self.WARN_COLOR,
            )
            return "break"

        self.end += 1
        self._refresh()
        return "break"

    def _on_shrink(self, event):
        if self.start is None:
            return "break"
        if self.end <= self.start + 1:
            self._set_status(
                text="One session left - press ctrl+n to skip the group instead",
                color=self.WARN_COLOR,
            )
            return "break"

        self.end -= 1
        self._refresh()
        return "break"

    def _on_skip(self, event):
        if self.start is None:
            return "break"
        # One session on, not the whole group: a proposal that started too
        # early is re-offered without its first session
        self.cursor = self.start + 1
        self._advance()
        return "break"

    def _on_accept(self, event):
        if self.start is None:
            return "break"

        name = self.clean_name(name=self.entry.get())
        if not name:
            self._set_status(
                text="Nothing to move to - the folder name is empty",
                color=self.ERROR_COLOR,
            )
            return "break"

        items = self.current_group
        folder_name = self.build_folder_name(name=name, items=items)
        folder = self.dir_path / folder_name
        try:
            folder.mkdir(exist_ok=True)
        except OSError as error:
            self._set_status(
                text=f"Could not create {folder_name}: {error}", color=self.ERROR_COLOR
            )
            return "break"

        moves, failures = self.move_group(items=items, folder=folder)
        if not moves:
            # Nothing went in, so leave no empty folder behind either
            self._remove_if_empty(folder=folder)
            self._set_status(
                text=f"Not one file could be moved: {failures[0]}",
                color=self.ERROR_COLOR,
            )
            return "break"

        self.last_move = {
            "folder": folder,
            "moves": moves,
            "start": self.start,
            "end": self.end,
            "name": name,
            "number": self.group_number,
        }
        self.moved_groups += 1
        self.moved_files += len(moves)

        self.cursor = self.end
        self._advance()

        message = (
            f"\u2713 Moved {self._count(number=len(moves), noun='file')} "
            f"\u2192 {folder_name}\\"
        )
        if failures:
            message += f"   ({len(failures)} left behind: {failures[0]})"
        self._set_status(
            text=message, color=self.WARN_COLOR if failures else self.PREVIEW_COLOR
        )
        return "break"

    @staticmethod
    def _remove_if_empty(*, folder: Path) -> None:
        try:
            folder.rmdir()
        # Something else ended up in there, which is reason enough to keep it
        except OSError:
            pass

    def _on_undo(self, event):
        if not self.last_move:
            self._set_status(text="Nothing to undo", color=self.HINT_COLOR)
            return "break"

        batch = self.last_move
        failures = []
        for item, old_path, _ in reversed(batch["moves"]):
            try:
                item.path = self._move_to(source=item.path, target=old_path)
            except OSError as error:
                failures.append(f"{item.path.name} ({error.strerror or error})")

        self._remove_if_empty(folder=batch["folder"])
        self.moved_groups -= 1
        self.moved_files -= len(batch["moves"]) - len(failures)
        self.last_move = None

        # Back to exactly the proposal that was accepted, edges and all
        self.entry.configure(state=tk.NORMAL)
        self.start, self.end = batch["start"], batch["end"]
        self.cursor = batch["start"]
        self.group_number = batch["number"]
        self._prefill_entry(name=batch["name"])
        self._refresh()

        message = f"\u21b6 Undone: {batch['folder'].name}\\"
        if failures:
            message += f"   ({len(failures)} stayed put: {failures[0]})"
        self._set_status(
            text=message, color=self.ERROR_COLOR if failures else self.WARN_COLOR
        )
        return "break"

    # -- entry points ------------------------------------------------------

    def _prepare(self) -> bool:
        """Read the folder and cluster it. ``False`` means there is nothing to do."""
        if not self.dir_path.is_dir():
            print(f"Not a directory: {self.dir_path}")
            return False

        self.items = self._collect_media()
        if not self.items:
            print(f"No pictures or videos found in {self.dir_path}")
            return False

        self.sessions = self._build_sessions(items=self.items)
        self.total_estimate = len(self._all_proposals())
        return True

    def report(self) -> None:
        """Print the grouping and change nothing - the way to check thresholds."""
        if not self._prepare():
            return

        photos = sum(1 for item in self.items if not item.is_video)
        print(self.dir_path)
        print(
            f"  {self._count(number=len(self.items), noun='file')} "
            f"({self._count(number=photos, noun='photo')}, "
            f"{self._count(number=len(self.items) - photos, noun='video')}) "
            f"in {self._count(number=len(self.sessions), noun='session')}"
        )
        print(
            f"  session gap {self._hours(self.session_gap)}h, "
            f"merge gap {self._hours(self.merge_gap)}h, "
            f"min group {self.min_event_size}"
        )
        # Where the dates came from decides how much the grouping is worth: a
        # folder resolved mostly from file dates clusters by when it was copied
        sources: dict[str, int] = {}
        for item in self.items:
            sources[item.source] = sources.get(item.source, 0) + 1
        print(
            "  dates from: "
            + ", ".join(
                f"{source} {count}"
                for source, count in sorted(
                    sources.items(), key=lambda pair: -pair[1]
                )
            )
        )
        print()

        grouped = 0
        proposals = self._all_proposals()
        for number, (start, end) in enumerate(proposals, start=1):
            items = [item for session in self.sessions[start:end] for item in session]
            grouped += len(items)
            sessions = "/".join(
                str(len(session)) for session in self.sessions[start:end]
            )
            days = (items[-1].taken.date() - items[0].taken.date()).days + 1
            span = (
                f"{days} days"
                if days > 1
                else f"{items[0].taken:%H:%M} - {items[-1].taken:%H:%M}"
            )
            print(
                f"  {number:>3}. {self.date_suffix(items=items)[1:-1]:<21} "
                f"{len(items):>4} files   {span:<16} sessions {sessions}"
            )

        print()
        print(
            f"  {self._count(number=len(proposals), noun='group')}, "
            f"{self._count(number=grouped, noun='file')}   \u00b7   "
            f"{self._count(number=len(self.items) - grouped, noun='file')} "
            f"left as singles or random shots"
        )

    @staticmethod
    def _hours(delta: timedelta) -> str:
        hours = delta.total_seconds() / 3600
        return f"{hours:g}"

    def process(self) -> None:
        if not self._prepare():
            return

        self._build_ui()
        threading.Thread(target=self._thumb_worker, daemon=True).start()
        self.window.after(50, self._advance)
        self.window.after(self.THUMB_POLL_MS, self._drain_thumbs)
        self.window.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find runs of pictures that belong together and file them away."
    )
    parser.add_argument("path", nargs="?", default=EventGrouper.DIR_PATH)
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the grouping and exit, touching nothing",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=EventGrouper._hours(EventGrouper.SESSION_GAP),
        help="hours without a picture that end a session",
    )
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=EventGrouper._hours(EventGrouper.MERGE_GAP),
        help="hours two sessions may be apart and still be one event",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=EventGrouper.MIN_EVENT_SIZE,
        help="smallest run still worth proposing as a group",
    )
    arguments = parser.parse_args()

    grouper = EventGrouper(
        dir_path=arguments.path,
        session_gap=timedelta(hours=float(arguments.gap)),
        merge_gap=timedelta(hours=float(arguments.merge_gap)),
        min_event_size=arguments.min,
    )
    if arguments.report:
        grouper.report()
    else:
        grouper.process()


if __name__ == "__main__":
    main()
