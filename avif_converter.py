import os
from pathlib import Path

from PIL import Image


class AvifConverter:
    """Convert every AVIF image in ``DIR_PATH`` to JPEG.

    Scans ``DIR_PATH`` for files with a ``.avif`` extension, opens each one,
    converts it to RGB and saves a ``.jpg`` copy alongside the original. The
    source ``.avif`` files are left untouched.
    """

    DIR_PATH = r"D:\path\to\dir"

    def process(self):
        """Convert all AVIF files in ``DIR_PATH`` and print the number converted."""
        conversion_counter = 0

        for element in os.scandir(Path(self.DIR_PATH)):
            if not element.is_file():
                continue
            extension = Path(element).suffix.lower()

            if extension not in (".avif",):
                continue

            with Image.open(element.path) as im:
                im.convert("RGB").save(Path(element.path).with_suffix(".jpg"), "JPEG")

            conversion_counter += 1

        print(f"{conversion_counter} files converted.")


if __name__ == "__main__":
    AvifConverter().process()
