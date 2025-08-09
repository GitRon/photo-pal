import os
import re
from pathlib import Path
from typing import Optional

import exifread


class PhotoTimestampRenamer:
    DIR_PATH = r"C:\Users\ronny\OneDrive\Desktop\HEIC Test"

    def get_exif_data(self, *, image_path) -> Optional[str]:
        # Öffne das Bild und lese die EXIF-Daten
        with open(image_path, "rb") as image_file:
            tags = exifread.process_file(image_file)

        # Hol das Aufnahmedatum aus den EXIF-Daten
        date_taken = tags.get("EXIF DateTimeOriginal")

        if date_taken:
            return str(date_taken)
        else:
            return None

    def process(self):
        failure_counter = 0
        rename_counter = 0

        for element in os.scandir(Path(self.DIR_PATH)):
            if not element.is_file():
                continue
            extension = Path(element).suffix.lower()
            if extension not in (".jpg", ".jpeg", ".heic"):
                continue

            date_taken = self.get_exif_data(image_path=element)
            if date_taken is None:
                continue

            pattern = r"^(\d{4}):(\d{2}):(\d{2})\s(\d{2}):(\d{2}):(\d{2})$"
            matches = re.search(pattern, date_taken)
            if not matches:
                continue

            year = matches[1]
            month = matches[2]
            day = matches[3]
            hour = matches[4]
            minute = matches[5]
            second = matches[6]

            new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}{extension}"
            try:
                os.rename(
                    element.path, element.path.replace(element.name, "") + new_filename
                )
                failure_counter = 0
            except FileExistsError:
                new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}-{failure_counter + 1}{extension}"
                os.rename(
                    element.path, element.path.replace(element.name, "") + new_filename
                )
            rename_counter += 1

        print(f"{rename_counter} files renamed.")


if __name__ == "__main__":
    PhotoTimestampRenamer().process()
