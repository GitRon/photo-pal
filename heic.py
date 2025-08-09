import datetime
import os
from pathlib import Path

import pyexiv2


class HeicImageFileRenamer:
    DIR_PATH = r"C:\Users\ronny\OneDrive\Desktop\HEIC Test"

    def process(self):
        failure_counter = 0
        rename_counter = 0

        for element in os.scandir(Path(self.DIR_PATH)):
            if not element.is_file():
                continue
            extension = Path(element).suffix.lower()

            if extension not in (".heic",):
                continue

            metadata = pyexiv2.Image(element.path).read_exif()
            date_taken = datetime.datetime.strptime(
                metadata["Exif.Image.DateTime"], "%Y:%m:%d %H:%M:%S"
            )

            sign = 1 if metadata["Exif.Photo.OffsetTimeOriginal"][0] == "+" else -1
            hours, minutes = map(
                int, metadata["Exif.Photo.OffsetTimeOriginal"][1:].split(":")
            )
            offset = datetime.timedelta(hours=hours, minutes=minutes) * sign
            date_taken = date_taken.replace(tzinfo=datetime.timezone(offset))

            new_filename = f"{date_taken.year}-{date_taken.month:02d}-{date_taken.day:02d} {date_taken.hour:02d}.{date_taken.minute:02d}.{date_taken.second:02d}{extension}"
            try:
                os.rename(
                    element.path, element.path.replace(element.name, "") + new_filename
                )
                failure_counter = 0
            except FileExistsError:
                new_filename = f"{date_taken.year}-{date_taken.month:02d}-{date_taken.day:02d} {date_taken.hour:02d}.{date_taken.minute:02d}.{date_taken.second:02d}-{failure_counter + 1}{extension}"
                os.rename(
                    element.path, element.path.replace(element.name, "") + new_filename
                )
            rename_counter += 1

        print(f"{rename_counter} files renamed.")


if __name__ == "__main__":
    HeicImageFileRenamer().process()
