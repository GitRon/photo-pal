import os
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()


class HeicConverter:
    """Convert every HEIC image in ``DIR_PATH`` to JPEG.

    Scans ``DIR_PATH`` for files with a ``.heic`` extension, opens each one,
    converts it to RGB and saves a ``.jpg`` copy alongside the original. Each
    source ``.heic`` file is deleted after it has been converted.
    """

    DIR_PATH = r"D:\Dropbox\Fotos\neu + unsortiert\B Merge notwendig\Griechenland (13.-29.4.26)\Carina"

    def process(self):
        """Convert all HEIC files in ``DIR_PATH`` and print the number converted."""
        conversion_counter = 0

        for element in os.scandir(Path(self.DIR_PATH)):
            if not element.is_file():
                continue
            extension = Path(element).suffix.lower()

            if extension not in (".heic",):
                continue

            with Image.open(element.path) as im:
                im.convert("RGB").save(Path(element.path).with_suffix(".jpg"), "JPEG")

            os.remove(element.path)

            conversion_counter += 1

        print(f"{conversion_counter} files converted.")


if __name__ == "__main__":
    HeicConverter().process()
