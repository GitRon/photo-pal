import os
import re
from pathlib import Path


class Renamer:
    FOTO_DIR = r"D:\Fotos\neu + unsortiert\B Merge notwendig\Hochzeit Luise & Otti (7.9.17)\Luise"

    PATTERN_OLD_SAMSUNG = r"^(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})%s$"
    PATTERN_GOOGLE = r"^PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\d{3}.*?%s$"
    PATTERN_WHATSAPP = r"^IMG-(\d{4})(\d{2})(\d{2})-W\d+%s$"
    PATTERN_ALEJANDRO = r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})%s$"
    PATTERN_FOTO = r"Foto\s(\d{2}).(\d{2}).(\d{2}),\s(\d{2})\s(\d{2})\s(\d{2})[\w\(\))]*%s$"

    def process(self):
        rename_counter = 0
        failure_counter = 0
        for element in os.scandir(Path(self.FOTO_DIR)):
            if element.is_file():
                extension = Path(element).suffix.lower()
                filename = element.name
                matches = re.search(self.PATTERN_FOTO % extension, filename)
                if not matches:
                    continue
                year = matches[1] if len(matches[1]) == 4 else f"20{matches[1]}"
                month = matches[2]
                day = matches[3]
                hour = matches[4]
                minute = matches[5]
                second = matches[6]
                new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}{extension}"
                try:
                    os.rename(element.path, element.path.replace(filename, '') + new_filename)
                    failure_counter = 0
                except FileExistsError:
                    new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}-{failure_counter + 1}{extension}"
                    os.rename(element.path, element.path.replace(filename, '') + new_filename)
                rename_counter += 1

        print(f"{rename_counter} files renamed.")


if __name__ == "__main__":
    Renamer().process()
