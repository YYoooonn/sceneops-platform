import json
import shutil
from pathlib import Path
from typing import Any


class ManifestStore:
    def __init__(self, version_root: Path) -> None:
        self.version_root = version_root

    def read_json(self, relative_path: str) -> Any | None:
        path = self.version_root / relative_path

        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def write_json(self, relative_path: str, data: Any) -> None:
        path = self.version_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")

    def reset(self) -> None:
        if self.version_root.exists():
            shutil.rmtree(self.version_root)

        self.version_root.mkdir(parents=True, exist_ok=True)

    def read_scene_index(self) -> list[dict[str, Any]]:
        data = self.read_json("scenes.json")

        if data is None:
            return []

        if not isinstance(data, list):
            raise ValueError("Invalid scenes.json format. Expected list.")

        return data

    def upsert_scene_index(
        self,
        new_scenes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        existing_scenes = self.read_scene_index()

        scene_map = {scene["sceneId"]: scene for scene in existing_scenes}

        for scene in new_scenes:
            scene_map[scene["sceneId"]] = scene

        merged = sorted(
            scene_map.values(),
            key=lambda scene: scene["sceneId"],
        )

        self.write_json("scenes.json", merged)

        return merged
