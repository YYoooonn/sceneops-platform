from fastapi import HTTPException, status


def raise_not_found(resource: str, resource_id: str | None = None) -> None:
    detail = f"{resource} not found"
    if resource_id is not None:
        detail = f"{resource} not found: {resource_id}"
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def raise_bad_request(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
