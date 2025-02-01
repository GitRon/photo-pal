import os
import re
from pathlib import Path


class Renamer:
    FOTO_DIR = r"C:\Users\Ronny\Desktop\falsch benannt"

    PATTERN_OLD_SAMSUNG = r"^(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})%s$"
    PATTERN_GOOGLE = r"^PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\d{3}.*?%s$"
    PATTERN_WHATSAPP = r"^IMG-(\d{4})(\d{2})(\d{2})-WA\d+%s$"
    PATTERN_ALEJANDRO = r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})%s$"
    PATTERN_FOTO = r"Foto\s(\d{2}).(\d{2}).(\d{2}),\s(\d{2})\s(\d{2})\s(\d{2})[\w\(\))]*%s$"

    def rename_image(self, *, element, filename, new_filename):
        os.rename(element.path, element.path.replace(filename, '') + new_filename)

    def process(self, name_pattern: str):
        rename_counter = 0
        failure_counter = 0
        for element in os.scandir(Path(self.FOTO_DIR)):
            if element.is_file():
                extension = Path(element).suffix.lower()
                filename = element.name
                matches = re.search(name_pattern % extension, filename)
                if not matches:
                    continue
                year = matches[1] if len(matches[1]) == 4 else f"20{matches[1]}"
                month = matches[2]
                day = matches[3]
                hour = matches[4] if matches.lastindex is not None and matches.lastindex >= 4 else "12"
                minute = matches[5] if matches.lastindex is not None and matches.lastindex >= 5 else "00"
                second = matches[6] if matches.lastindex is not None and matches.lastindex >= 6 else "00"
                new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}{extension}"
                rename_successful = False
                while not rename_successful:
                    try:
                        self.rename_image(element=element, filename=filename, new_filename=new_filename)
                        rename_successful = True
                        failure_counter = 0
                    except FileExistsError:
                        new_filename = f"{year}-{month}-{day} {hour}.{minute}.{second}-{failure_counter + 1}{extension}"
                        failure_counter += 1
                rename_counter += 1

        print(f"{rename_counter} files renamed.")


if __name__ == "__main__":
    Renamer().process(name_pattern=Renamer.PATTERN_WHATSAPP)
