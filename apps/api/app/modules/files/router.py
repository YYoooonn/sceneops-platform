from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.config import ApiSettings, get_settings
from app.modules.files.service import LocalFileService

router = APIRouter(prefix="/files", tags=["files"])


def get_file_service(
    settings: ApiSettings = Depends(get_settings),
) -> LocalFileService:
    return LocalFileService(settings.raw_data_root)


@router.get("/nuscenes")
def get_nuscenes_file(
    path: str,
    service: LocalFileService = Depends(get_file_service),
):
    file_path = service.resolve_nuscenes_file(path)
    return FileResponse(file_path)
