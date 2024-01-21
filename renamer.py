import os
import re
from pathlib import Path


class Renamer:
    FOTO_DIR = "D:\\Fotos\\neu + unsortiert\\A Sortierbar\\Kölnpfad mit Jessi 2021\\11 Etappe 08"

    PATTERN_OLD_SAMSUNG = r"^(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})%s$"
    PATTERN_WHATSAPP = r"^IMG-(\d{4})(\d{2})(\d{2})-W\d+%s$"

    def process(self):
        rename_counter = 0
        for element in os.scandir(Path(self.FOTO_DIR)):
            if element.is_file():
                extension = Path(element).suffix.lower()
                filename = element.name
                matches = re.search(self.PATTERN_OLD_SAMSUNG % extension, filename)
                if not matches:
                    continue
                year = matches[1]
                month = matches[2]
                day = matches[3]
                hour = matches[4]
                minute = matches[5]
                second = matches[6]
                new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}.jpg"
                os.rename(element.path, element.path.replace(filename, '') + new_filename)
                rename_counter += 1

        print(f"{rename_counter} files renamed.")


if __name__ == "__main__":
    Renamer().process()
