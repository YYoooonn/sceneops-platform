from pathlib import Path

from app.shared.errors import bad_request, not_found


class LocalFileService:
    def __init__(self, raw_data_root: Path) -> None:
        self.raw_data_root = raw_data_root

    def resolve_nuscenes_file(self, path: str) -> Path:
        safe_path = Path(path)

        if safe_path.is_absolute() or ".." in safe_path.parts:
            raise bad_request("Invalid file path")

        file_path = self.raw_data_root / "nuscenes" / safe_path

        if not file_path.exists():
            raise not_found("File not found")

        if not file_path.is_file():
            raise bad_request("Path is not a file")

        return file_path
