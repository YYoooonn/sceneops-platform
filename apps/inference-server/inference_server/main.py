from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from inference_server.grounding_dino import GroundingDinoModel
from inference_server.schemas import DetectRequest, DetectResponse

logger = logging.getLogger(__name__)

_model: GroundingDinoModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    logger.info("Loading GroundingDINO-T model...")
    _model = GroundingDinoModel()
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
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/v1/detect", response_model=DetectResponse)
async def detect(request: DetectRequest) -> DetectResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    try:
        detections, inference_ms = await asyncio.to_thread(_model.detect, request)
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
