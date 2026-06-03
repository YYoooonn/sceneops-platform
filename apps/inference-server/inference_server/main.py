from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from inference_server.config import get_settings
from inference_server.grounding_dino import GroundingDinoModel
from inference_server.schemas import DetectRequest, DetectResponse

logger = logging.getLogger(__name__)

_model: GroundingDinoModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    settings = get_settings()
    logger.info(
        "Loading %s (box_threshold=%.2f, text_threshold=%.2f, max_image_size=%d)",
        settings.model_id,
        settings.box_threshold,
        settings.text_threshold,
        settings.max_image_size,
    )
    _model = GroundingDinoModel(settings=settings)
    await asyncio.to_thread(_model.load)
    logger.info("Model loaded on device: %s", _model.device)
    yield
    _model = None


app = FastAPI(
    title="SceneOps Inference Server",
    description="GroundingDINO-T 2D detection endpoint for the SceneOps auto-label pipeline.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "model_id": settings.model_id,
        "device": _model.device if _model else None,
    }


@app.post("/v1/detect", response_model=DetectResponse)
async def detect(request: DetectRequest) -> DetectResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    settings = get_settings()

    # Apply server-side defaults for fields the caller left as None
    filled = request.model_copy(
        update={
            "box_threshold": request.box_threshold
            if request.box_threshold is not None
            else settings.box_threshold,
            "text_threshold": request.text_threshold
            if request.text_threshold is not None
            else settings.text_threshold,
            "max_image_size": request.max_image_size
            if request.max_image_size is not None
            else settings.max_image_size,
        }
    )

    try:
        detections, inference_ms = await asyncio.to_thread(_model.detect, filled)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DetectResponse(
        detections=detections,
        inference_ms=round(inference_ms, 2),
        device=_model.device,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
