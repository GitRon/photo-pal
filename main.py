import os
import random
from pathlib import Path


class PhotoPal:
    FOTO_PATH = "D:\\Fotos\\neu + unsortiert\\"

    IMAGE_TYPES = [".jpg", ".jpeg", ".png"]
    VIDEO_TYPES = [".mp4", ".mov", ".avi"]

    MAX_DIRS_TO_CHOSE_FROM = 7
    FRUIT_LENGTH = 10

    CHANCE_FOR_CONTAINER_1 = 50
    CHANCE_FOR_CONTAINER_2 = 35
    CHANCE_FOR_CONTAINER_3 = 15

    dir_map: dict = dict()
    overall_file_counter = 0

    def __init__(self) -> None:
        super().__init__()

    def _walk_directory(
            self,
            *,
            dir_entry: os.DirEntry,
            img_count: int = 0,
            video_count: int = 0,
            unknown_count: int = 0,
    ) -> tuple[int, int, int]:
        for element in os.scandir(dir_entry.path):
            if element.is_file():
                extension = Path(element).suffix.lower()
                if extension in self.IMAGE_TYPES:
                    img_count += 1
                    self.overall_file_counter += 1
                elif extension in self.VIDEO_TYPES:
                    video_count += 1
                    self.overall_file_counter += 1
                else:
                    print(f"> Unknown file detected: {element.path}")
                    unknown_count += 1
            elif element.is_dir():
                (img_count, video_count, unknown_count,) = self._walk_directory(
                    dir_entry=element,
                    img_count=img_count,
                    video_count=video_count,
                    unknown_count=unknown_count,
                )

        return img_count, video_count, unknown_count

    def _process_unsorted_category(self, container: str):
        self.dir_map[container] = []
        for unsorted_dir in os.scandir(f"{self.FOTO_PATH}{container}"):
            img_count, video_count, unknown_count = self._walk_directory(
                dir_entry=unsorted_dir
            )

            self.dir_map[container].append(
                {
                    "name": unsorted_dir.name,
                    "images": img_count,
                    "videos": video_count,
                    "unknown": unknown_count,
                    "total": img_count + video_count + unknown_count,
                }
            )

    def _pick_suggestion(self, container) -> dict:
        random_index = random.randint(
            0, min(len(container) - 1, self.MAX_DIRS_TO_CHOSE_FROM)
        )
        return container[random_index]

    def _prettify_suggestion_string(self, suggestion: dict, container: str) -> str:
        per_mille = 100 * (suggestion["images"] + suggestion["videos"]) / self.overall_file_counter
        return (
            f'\nUnser Vorschlag: Sortiere doch heute den Ordner "{container.split(" ")[1]}/{suggestion["name"]}" '
            f'({suggestion["images"]} Bilder und {suggestion["videos"]} Videos).\nDas sind {round(per_mille, 2)}% '
            f'aller noch zu sortierenden Dateien.'
        )

    def _calculate_suggestion_container(self, suggestion_list: list):
        # Make it more likely that we present an easier directory
        chance = random.randint(0, 100)
        if chance <= self.CHANCE_FOR_CONTAINER_1:
            suggestion_index = 0
        elif chance <= self.CHANCE_FOR_CONTAINER_2:
            suggestion_index = 1
        else:
            suggestion_index = 2
        print(suggestion_list[suggestion_index])

    def process(self):
        # Collect data
        for container in next(os.walk(self.FOTO_PATH))[1]:
            self._process_unsorted_category(container=container)

        # Sort by number of images and videos
        sorted_map = {}
        for key, container_dict in self.dir_map.items():
            sorted_map[key] = sorted(container_dict, key=lambda item: item["total"])

        # Stats
        print(f'\n| Container | Anzahl Ordner | Anzahl Dateien |')
        for container_name, container_list in sorted_map.items():
            dir_counter = 0
            item_counter = 0
            for directory in sorted_map[container_name]:
                dir_counter += 1
                item_counter += directory['total']
            print(f'| {container_name} | {dir_counter} | {item_counter} |')

        # Low-hanging fruit
        low_fruit_list = []
        for container_name, container_list in sorted_map.items():
            for directory in sorted_map[container_name]:
                if len(low_fruit_list) > self.FRUIT_LENGTH:
                    break
                directory['container_name'] = container_name
                low_fruit_list.append(directory)
        low_fruit_list = sorted(low_fruit_list, key=lambda item: item["total"])

        print(f'\n*Low-hanging fruits*')
        for fruit in low_fruit_list:
            print(f'"{fruit["container_name"].split(" ")[1]}/{fruit["name"]}" ({fruit["total"]} Elemente)')

        # Make a suggestion
        suggestion_list = []
        for key, container_dict in sorted_map.items():
            suggestion_list.append(
                self._prettify_suggestion_string(
                    self._pick_suggestion(container=container_dict), container=key
                )
            )
        self._calculate_suggestion_container(suggestion_list)


if __name__ == "__main__":
    PhotoPal().process()
