import os
from pathlib import Path

from PIL import Image


class AvifConverter:
    DIR_PATH = r"D:\path\to\dir"

    def process(self):
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
