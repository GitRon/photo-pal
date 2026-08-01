import os
import re
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, ImageTk
from pillow_heif import register_heif_opener

register_heif_opener()


class PictureNamer:
    """Interactive GUI to give standalone pictures a descriptive name.

    Shows every image of ``DIR_PATH`` one by one. Type a name into the text
    field and press ctrl+s to rename the file to ``<name> (dd.mm.YY)<ext>``.
    The date is the day the picture was taken, the extension is kept as is.
    """

    DIR_PATH = r"D:\Dropbox\Fotos\neu + unsortiert\C Chaos\Handy-Fotos 2024"

    IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".heic", ".avif", ".webp", ".gif")

    # Timestamps written by ``renamer.py`` / ``exif.py``: "2026-04-13 14.30.22"
    PATTERN_TIMESTAMP = r"^(\d{4})-(\d{2})-(\d{2})[\s_]"
    # Names this tool has written before: "Office at night (13.04.26)"
    PATTERN_NAMED = r"^(.*)\s\(\d{2}\.\d{2}\.\d{2}\)$"

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

    CACHE_SIZE = 5
    RESIZE_DEBOUNCE_MS = 100

    def __init__(self, *, dir_path: str) -> None:
        super().__init__()
        self.dir_path = Path(dir_path)
        self.image_paths: list[Path] = self._collect_images()
        self.current_index = 0
        # Keeps typed but not yet saved names, so navigating away loses nothing
        self.draft_map: dict[Path, str] = {}
        self.image_cache: dict[Path, Image.Image] = {}
        self.rendered_size: tuple[int, int] = (0, 0)
        self.rendered_path: Optional[Path] = None
        self.photo_image: Optional[ImageTk.PhotoImage] = None
        self.resize_job: Optional[str] = None

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

    # -- date resolution ---------------------------------------------------

    def _date_from_exif(self, *, image_path: Path) -> Optional[datetime]:
        try:
            with Image.open(image_path) as im:
                exif = im.getexif()
                date_taken = exif.get_ifd(self.EXIF_IFD).get(
                    self.EXIF_DATE_TIME_ORIGINAL
                ) or exif.get(self.EXIF_DATE_TIME)
        except (OSError, SyntaxError, ValueError):
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

    def get_date_taken(self, *, image_path: Path) -> tuple[datetime, str]:
        """Return the date of the picture plus where that date came from."""
        date_taken = self._date_from_exif(image_path=image_path)
        if date_taken:
            return date_taken, "EXIF"

        date_taken = self._date_from_filename(image_path=image_path)
        if date_taken:
            return date_taken, "filename"

        return datetime.fromtimestamp(image_path.stat().st_mtime), "file date"

    # -- filename building -------------------------------------------------

    def build_new_filename(self, *, image_path: Path, name: str) -> str:
        date_taken, _ = self.get_date_taken(image_path=image_path)
        clean_name = re.sub(self.FORBIDDEN_CHARS, "", name)
        clean_name = re.sub(r"\s+", " ", clean_name).strip().rstrip(".")
        return f"{clean_name} ({date_taken.strftime('%d.%m.%y')}){image_path.suffix}"

    def rename_image(self, *, image_path: Path, new_filename: str) -> Path:
        """Rename the file, dodging collisions with a ``-1``, ``-2``, ... suffix."""
        target = image_path.with_name(new_filename)
        stem = target.stem
        failure_counter = 0

        while True:
            if target == image_path:
                return image_path
            try:
                os.rename(image_path, target)
            except FileExistsError:
                failure_counter += 1
                target = target.with_name(
                    f"{stem}-{failure_counter}{image_path.suffix}"
                )
            else:
                return target

    # -- image loading -----------------------------------------------------

    def load_image(self, *, image_path: Path) -> Image.Image:
        if image_path not in self.image_cache:
            with Image.open(image_path) as im:
                # Phone pictures carry their orientation in the EXIF data
                self.image_cache[image_path] = ImageOps.exif_transpose(im)
            if len(self.image_cache) > self.CACHE_SIZE:
                self.image_cache.pop(next(iter(self.image_cache)))

        return self.image_cache[image_path]

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
                "ctrl+s save   esc quit"
            ),
            bg=self.BACKGROUND_COLOR,
            fg=self.HINT_COLOR,
            font=("Segoe UI", 9),
        ).pack(fill=tk.X, pady=(6, 0))

        self.entry.bind("<KeyRelease>", self._on_type)
        self.entry.bind("<Left>", self._on_left)
        self.entry.bind("<Right>", self._on_right)
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
        # Same picture at the same size is already on screen
        if (width, height) == self.rendered_size and (
            self.rendered_path == self.current_path
        ):
            return

        image = self.load_image(image_path=self.current_path).copy()
        image.thumbnail((width, height), Image.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(
            width // 2, height // 2, anchor=tk.CENTER, image=self.photo_image
        )
        self.rendered_size = (width, height)
        self.rendered_path = self.current_path

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

    def _update_preview(self):
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
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.draft_map.get(self.current_path, ""))
        self.entry.icursor(tk.END)
        self.entry.focus_set()
        self._render_image()
        self._render_footer()

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
        self._remember_draft()
        self.current_index = (self.current_index + step) % len(self.image_paths)
        self._show_current()
        return "break"

    def _on_save(self, event):
        name = self.entry.get().strip()
        if not re.sub(self.FORBIDDEN_CHARS, "", name).strip():
            self.preview_label.configure(
                text="Nothing to save - the name is empty", fg=self.ERROR_COLOR
            )
            return "break"

        image_path = self.current_path
        new_filename = self.build_new_filename(image_path=image_path, name=name)
        new_path = self.rename_image(image_path=image_path, new_filename=new_filename)

        self.image_cache.pop(image_path, None)
        self.draft_map.pop(image_path, None)
        self.image_paths[self.current_index] = new_path

        # Straight on to the next picture, that is where the speed comes from
        if len(self.image_paths) > 1:
            self._navigate(step=1)
        else:
            self._show_current()

        self.info_label.configure(text=f"Saved: {new_path.name}")
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
