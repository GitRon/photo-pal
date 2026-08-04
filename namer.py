import ctypes
import os
import re
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
from pillow_heif import register_heif_opener

register_heif_opener()

# -- recycle bin -----------------------------------------------------------
# Deleting through the shell rather than through ``os.remove`` is what puts a
# picture into the recycle bin instead of wiping it off the disk.

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


class ShFileOpStruct(ctypes.Structure):
    """Argument block of the Win32 ``SHFileOperationW`` call.

    ``fFlags`` is a 16 bit field followed by padding, so spending a full
    ``c_uint`` on it keeps every later member on the offset Windows expects.
    """

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_uint),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def move_to_recycle_bin(*, file_path: Path) -> None:
    """Move one file to the recycle bin, raising ``OSError`` if that fails."""
    if os.name != "nt":
        raise OSError("the recycle bin is only wired up for Windows")

    path = str(file_path.resolve())
    # A share has no recycle bin, and Windows would quietly delete for good
    if path.startswith("\\\\"):
        raise OSError("files on a network share cannot be recycled")

    operation = ShFileOpStruct(
        hwnd=None,
        wFunc=FO_DELETE,
        # The list of files to delete has to end on a second null character
        pFrom=f"{path}\0\0",
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    shell = ctypes.windll.shell32
    shell.SHFileOperationW.argtypes = [ctypes.POINTER(ShFileOpStruct)]
    shell.SHFileOperationW.restype = ctypes.c_int

    result = shell.SHFileOperationW(ctypes.byref(operation))
    if result:
        raise OSError(f"the shell refused to delete the file (code {result})")
    if operation.fAnyOperationsAborted:
        raise OSError("the move to the recycle bin was aborted")


class PictureNamer:
    """Interactive GUI to give standalone pictures a descriptive name.

    Shows every image of ``DIR_PATH`` one by one. Type a name into the text
    field and press ctrl+s to rename the file to ``<name> (dd.mm.YY)<ext>``.
    The date is the day the picture was taken, the extension is kept as is.

    A picture that needs no name at all can be filed away with a single key
    instead - see ``QUICK_FOLDERS``.
    """

    DIR_PATH = r"D:\Dropbox\Fotos\neu + unsortiert\C Chaos\Handy-Fotos 2023"

    IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".heic", ".avif", ".webp", ".gif")

    # Timestamps written by ``renamer.py`` / ``exif.py``: "2026-04-13 14.30.22"
    PATTERN_TIMESTAMP = r"^(\d{4})-(\d{2})-(\d{2})[\s_]"
    # Names this tool has written before: "Office at night (13.04.26)"
    PATTERN_NAMED = r"^(.*)\s\((\d{2})\.(\d{2})\.(\d{2})\)$"

    # "DateTimeOriginal" lives in the Exif sub-IFD, "DateTime" in the main one
    EXIF_IFD = 0x8769
    EXIF_DATE_TIME_ORIGINAL = 36867
    EXIF_DATE_TIME = 306

    FORBIDDEN_CHARS = r'[\\/:*?"<>|]'

    # -- filing away -------------------------------------------------------
    # One key moves the picture into ``<prefix> <year>/`` next to the folder it
    # came from, without renaming anything: pictures of the flat or the office
    # are documentation, and a name would tell you nothing a date does not.
    # The key only reaches the picture while the name field is empty, the same
    # rule del follows - alt+key works either way.
    QUICK_FOLDERS = (("b", "Büro"), ("w", "Wohnung"))

    # Bit Tk sets in ``event.state`` while ctrl is held down
    CONTROL_MASK = 0x0004

    BACKGROUND_COLOR = "#1e1e1e"
    TEXT_COLOR = "#dddddd"
    HINT_COLOR = "#888888"
    PREVIEW_COLOR = "#7ec699"
    ERROR_COLOR = "#e06c75"
    TRASH_COLOR = "#d19a66"
    FILED_COLOR = "#61afef"

    CACHE_SIZE = 5
    RESIZE_DEBOUNCE_MS = 100

    # -- "next up" panel, in its own column right of the picture -----------
    PREVIEW_COUNT = 3
    TILE_SIZE = (150, 106)
    # The picture sits inside the tile, so the tile border stays visible
    THUMB_SIZE = (138, 94)
    PANEL_PADDING = 10
    PANEL_MARGIN = 14
    TILE_GAP = 8
    HEADER_HEIGHT = 20
    THUMB_CACHE_SIZE = 16
    # Below this the column would cost the picture more than it is worth
    MIN_PICTURE_WIDTH = 380
    # The panel no longer sits on the picture, so it may be solid: a card
    # lifted off the background, with the tiles sunk back into it
    PANEL_FILL = (38, 38, 38, 255)
    PANEL_OUTLINE = (255, 255, 255, 20)
    TILE_FILL = (0, 0, 0, 120)
    TILE_OUTLINE = (255, 255, 255, 40)

    def __init__(self, *, dir_path: str) -> None:
        super().__init__()
        self.dir_path = Path(dir_path)
        self.image_paths: list[Path] = self._collect_images()
        self.current_index = 0
        # Keeps typed but not yet saved names, so navigating away loses nothing
        self.draft_map: dict[Path, str] = {}
        self.image_cache: dict[Path, Image.Image] = {}
        # Small previews are kept apart from the full images: the panel would
        # otherwise push the picture you are looking at out of the cache
        self.thumb_cache: dict[Path, Optional[Image.Image]] = {}
        self.font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}
        self.rendered_size: tuple[int, int] = (0, 0)
        self.rendered_key: Optional[tuple] = None
        self.photo_image: Optional[ImageTk.PhotoImage] = None
        self.resize_job: Optional[str] = None
        # Field content at the moment a message was put on the preview line.
        # Every shortcut ends with a key release, and that release must not
        # wipe the confirmation the shortcut just wrote.
        self.status_entry_text: Optional[str] = None

    def _collect_images(self) -> list[Path]:
        images = [
            Path(element.path)
            for element in os.scandir(self.dir_path)
            if element.is_file() and Path(element).suffix.lower() in self.IMAGE_TYPES
        ]
        return sorted(images, key=lambda path: path.name.lower())

    @property
    def current_path(self) -> Path:
        return self.image_paths[self.current_index]

    def next_paths(self) -> list[Path]:
        """The pictures coming up, at most ``PREVIEW_COUNT``, never the current one."""
        paths = []
        for step in range(1, self.PREVIEW_COUNT + 1):
            index = (self.current_index + step) % len(self.image_paths)
            # Fewer pictures than preview slots: stop before wrapping onto self
            if index == self.current_index:
                break
            paths.append(self.image_paths[index])
        return paths

    # -- date resolution ---------------------------------------------------

    def _date_from_exif(self, *, image_path: Path) -> Optional[datetime]:
        try:
            with Image.open(image_path) as im:
                exif = im.getexif()
                date_taken = exif.get_ifd(self.EXIF_IFD).get(
                    self.EXIF_DATE_TIME_ORIGINAL
                ) or exif.get(self.EXIF_DATE_TIME)
        # Broken or half written EXIF blocks raise all sorts of things, and a
        # missing date is not a reason to give up on the picture
        except (OSError, SyntaxError, ValueError, TypeError, KeyError):
            return None

        if not date_taken:
            return None

        try:
            return datetime.strptime(str(date_taken), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return None

    def _date_from_filename(self, *, image_path: Path) -> Optional[datetime]:
        matches = re.search(self.PATTERN_TIMESTAMP, image_path.name)
        if not matches:
            return None

        try:
            return datetime(int(matches[1]), int(matches[2]), int(matches[3]))
        except ValueError:
            return None

    def _date_from_own_name(self, *, image_path: Path) -> Optional[datetime]:
        matches = re.search(self.PATTERN_NAMED, image_path.stem)
        if not matches:
            return None

        try:
            return datetime.strptime(
                f"{matches[2]}.{matches[3]}.{matches[4]}", "%d.%m.%y"
            )
        except ValueError:
            return None

    def get_date_taken(self, *, image_path: Path) -> tuple[datetime, str]:
        """Return the date of the picture plus where that date came from."""
        date_taken = self._date_from_exif(image_path=image_path)
        if date_taken:
            return date_taken, "EXIF"

        date_taken = self._date_from_filename(image_path=image_path)
        if date_taken:
            return date_taken, "filename"

        # A picture named earlier already carries the date we worked out back
        # then. Without this a rename would reset it to today's file date.
        date_taken = self._date_from_own_name(image_path=image_path)
        if date_taken:
            return date_taken, "existing name"

        return datetime.fromtimestamp(image_path.stat().st_mtime), "file date"

    # -- filename building -------------------------------------------------

    def build_new_filename(self, *, image_path: Path, name: str) -> str:
        date_taken, _ = self.get_date_taken(image_path=image_path)
        clean_name = re.sub(self.FORBIDDEN_CHARS, "", name)
        clean_name = re.sub(r"\s+", " ", clean_name).strip().rstrip(".")
        return f"{clean_name} ({date_taken.strftime('%d.%m.%y')}){image_path.suffix}"

    @staticmethod
    def _move_file(*, source: Path, target: Path) -> Path:
        """Move the file, dodging collisions with a ``-1``, ``-2``, ... suffix.

        Asking ``os.rename`` and catching the refusal beats looking the target
        up first: between the look and the move something else could take the
        name, and the picture would be gone.
        """
        stem, suffix = target.stem, target.suffix
        failure_counter = 0

        while True:
            if target == source:
                return source
            try:
                os.rename(source, target)
            except FileExistsError:
                failure_counter += 1
                target = target.with_name(f"{stem}-{failure_counter}{suffix}")
            else:
                return target

    def rename_image(self, *, image_path: Path, new_filename: str) -> Path:
        return self._move_file(
            source=image_path, target=image_path.with_name(new_filename)
        )

    # -- image loading -----------------------------------------------------

    def load_image(self, *, image_path: Path) -> Image.Image:
        if image_path not in self.image_cache:
            with Image.open(image_path) as im:
                # Phone pictures carry their orientation in the EXIF data
                self.image_cache[image_path] = ImageOps.exif_transpose(im)
            if len(self.image_cache) > self.CACHE_SIZE:
                self.image_cache.pop(next(iter(self.image_cache)))

        return self.image_cache[image_path]

    def load_thumbnail(self, *, image_path: Path) -> Optional[Image.Image]:
        """A small preview, or ``None`` if the file cannot be read at all."""
        if image_path in self.thumb_cache:
            return self.thumb_cache[image_path]

        try:
            with Image.open(image_path) as im:
                # Lets the JPEG decoder skip straight to a small size
                im.draft("RGB", self.THUMB_SIZE)
                thumb = ImageOps.exif_transpose(im)
                thumb.thumbnail(self.THUMB_SIZE, Image.LANCZOS)
                thumb = thumb.convert("RGB")
        except (OSError, SyntaxError, ValueError):
            thumb = None

        self.thumb_cache[image_path] = thumb
        if len(self.thumb_cache) > self.THUMB_CACHE_SIZE:
            self.thumb_cache.pop(next(iter(self.thumb_cache)))

        return thumb

    # -- drawing helpers ---------------------------------------------------

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

    def _preview_panel_size(self, *, count: int) -> tuple[int, int]:
        tile_width, tile_height = self.TILE_SIZE
        padding = self.PANEL_PADDING
        return (
            tile_width + 2 * padding,
            2 * padding
            + self.HEADER_HEIGHT
            + count * tile_height
            + (count - 1) * self.TILE_GAP,
        )

    def _build_preview_panel(self, *, paths: list[Path]) -> Image.Image:
        """The stack of upcoming pictures, drawn as a card of its own."""
        tile_width, tile_height = self.TILE_SIZE
        padding = self.PANEL_PADDING
        width, height = self._preview_panel_size(count=len(paths))

        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=12,
            fill=self.PANEL_FILL,
            outline=self.PANEL_OUTLINE,
        )
        draw.text(
            (padding + 2, padding),
            "NEXT UP",
            font=self._font(size=10, bold=True),
            fill=self._rgb(color=self.HINT_COLOR) + (255,),
        )

        accent = self._rgb(color=self.PREVIEW_COLOR)
        top = padding + self.HEADER_HEIGHT
        for position, image_path in enumerate(paths, start=1):
            tile = (padding, top, padding + tile_width, top + tile_height)
            draw.rounded_rectangle(tile, radius=8, fill=self.TILE_FILL)

            thumb = self.load_thumbnail(image_path=image_path)
            if thumb:
                panel.paste(
                    thumb,
                    (
                        tile[0] + (tile_width - thumb.width) // 2,
                        tile[1] + (tile_height - thumb.height) // 2,
                    ),
                )
            else:
                self._draw_centered(
                    draw=draw,
                    box=tile,
                    text="?",
                    font=self._font(size=18),
                    fill=self._rgb(color=self.ERROR_COLOR) + (220,),
                )

            # The one you land on next is highlighted, the rest stay quiet
            is_next = position == 1
            draw.rounded_rectangle(
                tile,
                radius=8,
                outline=accent + (230,) if is_next else self.TILE_OUTLINE,
                width=2 if is_next else 1,
            )

            badge = (tile[0] + 4, tile[1] + 4, tile[0] + 22, tile[1] + 22)
            draw.ellipse(
                badge,
                fill=accent + (235,) if is_next else (0, 0, 0, 170),
            )
            self._draw_centered(
                draw=draw,
                box=badge,
                text=str(position),
                font=self._font(size=10, bold=True),
                fill=(20, 20, 20, 255) if is_next else (235, 235, 235, 255),
            )

            top += tile_height + self.TILE_GAP

        return panel

    # -- ui ----------------------------------------------------------------

    def _build_ui(self):
        self.window = tk.Tk()
        self.window.title(f"Picture Namer - {self.dir_path}")
        self.window.geometry("1100x850")
        self.window.configure(bg=self.BACKGROUND_COLOR)

        # A Canvas keeps the requested size it is given and ignores what is
        # drawn on it. A Label would instead request the size of its image,
        # which makes pack hand out ever more space on every single render.
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
                "\u2190/\u2192 navigate (alt+\u2190/\u2192 always)   "
                "ctrl+s save   del recycle bin   "
                + "   ".join(
                    f"{key} \u2192 {prefix} <year>"
                    for key, prefix in self.QUICK_FOLDERS
                )
                + "   (alt+key works while typing, too)   esc quit"
            ),
            bg=self.BACKGROUND_COLOR,
            fg=self.HINT_COLOR,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(6, 0))

        self.entry.bind("<KeyRelease>", self._on_type)
        self.entry.bind("<Left>", self._on_left)
        self.entry.bind("<Right>", self._on_right)
        self.entry.bind("<Delete>", self._on_delete)
        self.window.bind("<Alt-Delete>", lambda event: self._trash_current())
        for key, prefix in self.QUICK_FOLDERS:
            # The bare key goes to the field, which passes it on only when there
            # is no name in it. Alt reaches the field as well - and once the
            # field has handled it, the window binding no longer fires.
            self.entry.bind(
                f"<KeyPress-{key}>",
                lambda event, prefix=prefix: self._on_quick_key(
                    event=event, folder_prefix=prefix
                ),
            )
            for pattern in (f"<Alt-{key}>", f"<Alt-{key.upper()}>"):
                self.window.bind(
                    pattern,
                    lambda event, prefix=prefix: self._file_away(folder_prefix=prefix),
                )
        self.window.bind("<Control-s>", self._on_save)
        self.window.bind("<Control-S>", self._on_save)
        self.window.bind("<Alt-Left>", lambda event: self._navigate(step=-1))
        self.window.bind("<Alt-Right>", lambda event: self._navigate(step=1))
        self.window.bind("<Prior>", lambda event: self._navigate(step=-1))
        self.window.bind("<Next>", lambda event: self._navigate(step=1))
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.canvas.bind("<Configure>", self._on_resize)

    def _render_image(self):
        self.resize_job = None
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 2 or height < 2:
            return

        has_pictures = bool(self.image_paths)
        next_paths = self.next_paths() if has_pictures else []
        key = (
            width,
            height,
            self.current_path if has_pictures else None,
            tuple(next_paths),
        )
        # The very same thing is already on screen
        if key == self.rendered_key:
            return

        # Picture and panel are composited into one image and handed to the
        # canvas as a whole. Keeps the two from ever disagreeing about who
        # owns which pixel, and lets the panel carry rounded corners.
        frame = Image.new("RGB", (width, height), self.BACKGROUND_COLOR)
        if not has_pictures:
            self._draw_centered(
                draw=ImageDraw.Draw(frame),
                box=(0, 0, width, height),
                text="No pictures left in this folder",
                font=self._font(size=16),
                fill=self._rgb(color=self.HINT_COLOR),
            )
        else:
            # The panel gets its own column, the picture takes what is left of
            # the width - so nothing is ever hidden behind it
            panel_count = self._fitting_preview_count(paths=next_paths, height=height)
            column_width = 0
            if panel_count:
                column_width = (
                    self._preview_panel_size(count=panel_count)[0]
                    + 2 * self.PANEL_MARGIN
                )
                if width - column_width < self.MIN_PICTURE_WIDTH:
                    column_width = 0

            self._paste_picture(frame=frame, area_width=width - column_width)
            if column_width:
                self._paste_preview_panel(frame=frame, paths=next_paths[:panel_count])

        self.photo_image = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image)
        self.rendered_size = (width, height)
        self.rendered_key = key

    def _fitting_preview_count(self, *, paths: list[Path], height: int) -> int:
        """How many previews fit the window height - fewer beats none."""
        for count in range(len(paths), 0, -1):
            _, panel_height = self._preview_panel_size(count=count)
            if panel_height + 2 * self.PANEL_MARGIN <= height:
                return count
        return 0

    def _paste_picture(self, *, frame: Image.Image, area_width: int) -> None:
        try:
            image = self.load_image(image_path=self.current_path).copy()
        except (OSError, SyntaxError, ValueError):
            # A file we cannot decode must not take the whole tool down - you
            # can still read its name in the footer and skip past it
            self._draw_centered(
                draw=ImageDraw.Draw(frame),
                box=(0, 0, area_width, frame.height),
                text="This picture cannot be displayed",
                font=self._font(size=16),
                fill=self._rgb(color=self.ERROR_COLOR),
            )
            return

        image.thumbnail((area_width, frame.height), Image.LANCZOS)
        position = (
            (area_width - image.width) // 2,
            (frame.height - image.height) // 2,
        )
        if image.mode in ("RGBA", "LA", "P"):
            # Transparent PNGs and GIFs keep the dark background behind them
            image = image.convert("RGBA")
            frame.paste(image, position, image)
        else:
            frame.paste(image.convert("RGB"), position)

    def _paste_preview_panel(self, *, frame: Image.Image, paths: list[Path]) -> None:
        panel = self._build_preview_panel(paths=paths)
        frame.paste(
            panel,
            (frame.width - panel.width - self.PANEL_MARGIN, self.PANEL_MARGIN),
            panel,
        )

    def _render_footer(self):
        date_taken, source = self.get_date_taken(image_path=self.current_path)
        self.info_label.configure(
            text=(
                f"{self.current_index + 1} / {len(self.image_paths)}   \u00b7   "
                f"{self.current_path.name}   \u00b7   "
                f"{date_taken.strftime('%d.%m.%y')} ({source})"
            )
        )
        self._update_preview()

    def _set_status(self, *, text: str, color: str):
        """Put a message on the preview line and let it stand until you type."""
        self.preview_label.configure(text=text, fg=color)
        self.status_entry_text = self.entry.get()

    def _update_preview(self):
        if self.status_entry_text is not None:
            # Nothing was typed since the message went up, so it stays
            if self.entry.get() == self.status_entry_text:
                return
            self.status_entry_text = None

        name = self.entry.get().strip()
        if not name:
            self.preview_label.configure(
                text="Type a name, then press ctrl+s", fg=self.HINT_COLOR
            )
            return

        new_filename = self.build_new_filename(image_path=self.current_path, name=name)
        self.preview_label.configure(
            text=f"\u2192 {new_filename}", fg=self.PREVIEW_COLOR
        )

    def _show_current(self):
        # Leaving the picture ends whatever message was standing about it
        self.status_entry_text = None
        if not self.image_paths:
            self._show_empty()
            return

        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.draft_map.get(self.current_path, ""))
        self.entry.icursor(tk.END)
        self.entry.focus_set()
        self._render_image()
        self._render_footer()

    def _show_empty(self):
        """Every picture of the folder has been sent to the recycle bin."""
        self.entry.delete(0, tk.END)
        self.entry.configure(state=tk.DISABLED)
        self.info_label.configure(text="0 / 0")
        self._set_status(text="Nothing left to name here", color=self.HINT_COLOR)
        self._render_image()

    def _remember_draft(self):
        name = self.entry.get().strip()
        if name:
            self.draft_map[self.current_path] = name
        else:
            self.draft_map.pop(self.current_path, None)

    # -- event handlers ----------------------------------------------------

    def _on_type(self, event):
        self._update_preview()

    def _on_resize(self, event):
        if (event.width, event.height) == self.rendered_size:
            return
        # Redraw once the dragging stops, not on every pixel of the way
        if self.resize_job is not None:
            self.window.after_cancel(self.resize_job)
        self.resize_job = self.window.after(self.RESIZE_DEBOUNCE_MS, self._render_image)

    def _on_left(self, event):
        # Only leave the picture when the cursor sits at the very beginning
        if self.entry.selection_present() or self.entry.index(tk.INSERT) > 0:
            return None
        self._navigate(step=-1)
        return "break"

    def _on_right(self, event):
        if self.entry.selection_present() or self.entry.index(tk.INSERT) < len(
            self.entry.get()
        ):
            return None
        self._navigate(step=1)
        return "break"

    def _navigate(self, *, step: int):
        if not self.image_paths:
            return "break"
        self._remember_draft()
        self.current_index = (self.current_index + step) % len(self.image_paths)
        self._show_current()
        return "break"

    def _on_delete(self, event):
        # Same rule as the arrow keys: the field comes first, so del only
        # reaches the picture once there is no text left for it to edit
        if self.entry.get() or self.entry.selection_present():
            return None
        return self._trash_current()

    def _forget_current(self):
        """Take the current picture out of the walk, caches and draft with it."""
        image_path = self.current_path
        self.image_cache.pop(image_path, None)
        self.thumb_cache.pop(image_path, None)
        self.draft_map.pop(image_path, None)
        self.image_paths.pop(self.current_index)
        # The picture that slid into this slot is the one to look at next, and
        # after the last one that is the first again
        if self.current_index >= len(self.image_paths):
            self.current_index = 0

    def _on_quick_key(self, *, event, folder_prefix: str):
        # Same rule as del: the field comes first, so the letter only reaches
        # the picture once there is no name for it to be part of. Ctrl is left
        # alone as well - ctrl+b moves the cursor, it does not file anything.
        if event.state & self.CONTROL_MASK:
            return None
        if self.entry.get() or self.entry.selection_present():
            return None
        return self._file_away(folder_prefix=folder_prefix)

    def _file_away(self, *, folder_prefix: str):
        """Move the current picture into ``<prefix> <year>/`` and move on."""
        if not self.image_paths:
            return "break"

        image_path = self.current_path
        date_taken, _ = self.get_date_taken(image_path=image_path)
        folder = self.dir_path / f"{folder_prefix} {date_taken.year}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            new_path = self._move_file(
                source=image_path, target=folder / image_path.name
            )
        except OSError as error:
            self._set_status(
                text=f"Could not move {image_path.name} to {folder.name}: {error}",
                color=self.ERROR_COLOR,
            )
            return "break"

        self._forget_current()
        self._show_current()
        self._set_status(
            text=f"\U0001f4c1 {folder.name}\\{new_path.name}", color=self.FILED_COLOR
        )
        return "break"

    def _trash_current(self):
        """Hand the current picture to the recycle bin and move on."""
        if not self.image_paths:
            return "break"

        image_path = self.current_path
        try:
            move_to_recycle_bin(file_path=image_path)
        except OSError as error:
            self._set_status(
                text=f"Could not delete {image_path.name}: {error}",
                color=self.ERROR_COLOR,
            )
            return "break"

        self._forget_current()
        self._show_current()
        self._set_status(
            text=f"\U0001f5d1 Recycle bin: {image_path.name}", color=self.TRASH_COLOR
        )
        return "break"

    def _on_save(self, event):
        if not self.image_paths:
            return "break"

        name = self.entry.get().strip()
        if not re.sub(self.FORBIDDEN_CHARS, "", name).strip():
            self._set_status(
                text="Nothing to save - the name is empty", color=self.ERROR_COLOR
            )
            return "break"

        image_path = self.current_path
        new_filename = self.build_new_filename(image_path=image_path, name=name)
        new_path = self.rename_image(image_path=image_path, new_filename=new_filename)

        self.image_cache.pop(image_path, None)
        self.draft_map.pop(image_path, None)
        # The preview is still valid, only its key moved with the rename
        thumb = self.thumb_cache.pop(image_path, None)
        if thumb is not None:
            self.thumb_cache[new_path] = thumb
        self.image_paths[self.current_index] = new_path

        # Straight on to the next picture, that is where the speed comes from
        if len(self.image_paths) > 1:
            self._navigate(step=1)
        else:
            self._show_current()

        # Confirm on the preview line, never on the info line: that one has to
        # keep describing the picture you are looking at now. Typing the next
        # name replaces this message on its own.
        self._set_status(text=f"✓ Saved: {new_path.name}", color=self.PREVIEW_COLOR)
        return "break"

    def process(self):
        if not self.dir_path.is_dir():
            print(f"Not a directory: {self.dir_path}")
            return
        if not self.image_paths:
            print(f"No images found in {self.dir_path}")
            return

        self._build_ui()
        # Prefill names this tool has written before, so they can be corrected
        for image_path in self.image_paths:
            matches = re.search(self.PATTERN_NAMED, image_path.stem)
            if matches:
                self.draft_map[image_path] = matches[1]

        self.window.after(50, self._show_current)
        self.window.mainloop()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else PictureNamer.DIR_PATH
    PictureNamer(dir_path=path).process()
