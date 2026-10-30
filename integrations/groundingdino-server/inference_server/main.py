from __future__ import annotations

import asyncio
import functools
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from inference_server.config import InferenceServerSettings, get_settings
from inference_server.storage import build_s3_client
from inference_server.grounding_dino import GroundingDinoModel
from inference_server.image_resolver import (
    ImageDecodeError,
    ImageIntegrityError,
    ImageResolver,
)
from inference_server.schemas import (
    DetectRequest,
    DetectResponse,
    HealthResponse,
    ReadyResponse,
)

logger = logging.getLogger(__name__)

_model: GroundingDinoModel | None = None
_image_resolver: ImageResolver | None = None
_warmup_state: _WarmupState | None = None
_inference_sem: asyncio.Semaphore | None = None
_concurrency: _ConcurrencyState | None = None


@dataclass
class _ConcurrencyState:
    max_requests: int
    active_requests: int = 0


@dataclass
class _WarmupState:
    enabled: bool
    completed: bool = False
    succeeded: bool | None = None  # None when warmup was not attempted
    elapsed_ms: float | None = None
    error: str | None = None


async def _do_warmup(
    model: GroundingDinoModel,
    settings: InferenceServerSettings,
) -> _WarmupState:
    """Run warmup inference and return the resulting state. Never raises."""
    state = _WarmupState(enabled=True)
    t0 = time.perf_counter()
    try:
        await asyncio.to_thread(
            functools.partial(
                model.warmup,
                image_size=settings.warmup_image_size,
                text_prompt=settings.warmup_text_prompt,
            )
        )
        state.completed = True
        state.succeeded = True
        state.elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        logger.info("Warmup completed in %.1f ms on %s", state.elapsed_ms, model.device)
    except Exception as exc:
        state.completed = True
        state.succeeded = False  # type: ignore[assignment]
        state.elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        state.error = str(exc)
        logger.warning("Warmup failed: %s", exc, exc_info=True)
    return state


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _image_resolver, _warmup_state, _inference_sem, _concurrency
    settings = get_settings()
    s3_client = build_s3_client(settings)
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

    _image_resolver = ImageResolver(
        allowed_roots=settings.allowed_file_roots,
        s3_client=s3_client,
    )
    logger.info(
        "ImageResolver initialized (allowed_roots=%s)", settings.allowed_file_roots
    )

    if settings.enable_warmup:
        _warmup_state = await _do_warmup(_model, settings)
    else:
        logger.info("Warmup disabled (enable_warmup=False)")
        _warmup_state = _WarmupState(enabled=False)

    _inference_sem = asyncio.Semaphore(settings.max_concurrent_inference_requests)
    _concurrency = _ConcurrencyState(
        max_requests=settings.max_concurrent_inference_requests
    )
    logger.info(
        "Inference semaphore initialized (max_concurrent=%d)",
        settings.max_concurrent_inference_requests,
    )

    yield

    _model = None
    _image_resolver = None
    _warmup_state = None
    _inference_sem = None
    _concurrency = None


# Image error codes (``detail.code`` of a 4xx /v1/detect response). The model is
# never invoked for any of them; clients tell integrity failures (not_found,
# integrity_mismatch) apart from undecodable images and from model errors (5xx).
IMAGE_NOT_FOUND = "image_not_found"
IMAGE_INTEGRITY_MISMATCH = "image_integrity_mismatch"
IMAGE_ACCESS_DENIED = "image_access_denied"
IMAGE_DECODE_FAILED = "image_decode_failed"
INVALID_IMAGE_URI = "invalid_image_uri"


def _image_error(status_code: int, code: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": str(exc)}
    )


app = FastAPI(
    title="SceneOps Inference Server",
    description="GroundingDINO-T 2D detection endpoint for the SceneOps auto-label pipeline.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Process liveness check. Returns 200 as long as the server process is running."""
    return HealthResponse()


@app.get("/readyz", response_model=ReadyResponse)
async def readyz() -> ReadyResponse:
    """Model readiness check. Returns 503 until the model is loaded and warmup policy is satisfied."""
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail=ReadyResponse(
                status="not_ready",
                model_loaded=False,
                reason="model is not loaded",
            ).model_dump(),
        )

    settings = get_settings()
    ws = _warmup_state

    # Block readiness when warmup failure is required to be fatal.
    if (
        ws is not None
        and ws.enabled
        and ws.completed
        and not ws.succeeded
        and settings.require_warmup_success_for_ready
    ):
        raise HTTPException(
            status_code=503,
            detail=ReadyResponse(
                status="not_ready",
                model_loaded=True,
                warmup_enabled=True,
                warmup_completed=True,
                warmup_succeeded=False,
                warmup_elapsed_ms=ws.elapsed_ms,
                warmup_error=ws.error,
                reason="warmup failed",
            ).model_dump(),
        )

    cs = _concurrency
    return ReadyResponse(
        status="ready",
        model_loaded=True,
        warmup_enabled=ws.enabled if ws is not None else False,
        warmup_completed=ws.completed if ws is not None else False,
        warmup_succeeded=ws.succeeded if ws is not None else None,
        warmup_elapsed_ms=ws.elapsed_ms if ws is not None else None,
        warmup_error=ws.error if ws is not None else None,
        max_concurrent_inference_requests=cs.max_requests if cs is not None else None,
        active_inference_requests=cs.active_requests if cs is not None else None,
        device=_model.device,
        model_id=settings.model_id,
    )


@app.post("/v1/detect", response_model=DetectResponse)
async def detect(request: DetectRequest) -> DetectResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if _inference_sem is None or _concurrency is None or _image_resolver is None:
        raise HTTPException(status_code=503, detail="Server not initialized")

    settings = get_settings()
    box_threshold = (
        request.box_threshold
        if request.box_threshold is not None
        else settings.box_threshold
    )
    text_threshold = (
        request.text_threshold
        if request.text_threshold is not None
        else settings.text_threshold
    )
    max_image_size = (
        request.max_image_size
        if request.max_image_size is not None
        else settings.max_image_size
    )

    resolver = _image_resolver
    model = _model

    async with _inference_sem:
        _concurrency.active_requests += 1
        logger.debug(
            "Inference started (active=%d/%d)",
            _concurrency.active_requests,
            _concurrency.max_requests,
        )
        try:
            # Only resolution errors are image errors: they are mapped here,
            # before the model runs. Model failures propagate (500).
            try:
                image = await asyncio.to_thread(
                    functools.partial(
                        resolver.resolve,
                        request.image_uri,
                        checksum=request.image_checksum,
                        size_bytes=request.image_size_bytes,
                    )
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "Image not found uri=%r trace_id=%r: %s",
                    request.image_uri,
                    request.trace_id,
                    exc,
                )
                raise _image_error(422, IMAGE_NOT_FOUND, exc) from exc
            except ImageIntegrityError as exc:
                logger.warning(
                    "Image does not match its pin uri=%r trace_id=%r: %s",
                    request.image_uri,
                    request.trace_id,
                    exc,
                )
                raise _image_error(422, IMAGE_INTEGRITY_MISMATCH, exc) from exc
            except PermissionError as exc:
                logger.warning(
                    "Image outside allowed roots uri=%r trace_id=%r: %s",
                    request.image_uri,
                    request.trace_id,
                    exc,
                )
                raise _image_error(403, IMAGE_ACCESS_DENIED, exc) from exc
            except ImageDecodeError as exc:
                logger.warning(
                    "Image not decodable uri=%r trace_id=%r: %s",
                    request.image_uri,
                    request.trace_id,
                    exc,
                )
                raise _image_error(422, IMAGE_DECODE_FAILED, exc) from exc
            except ValueError as exc:
                logger.warning(
                    "Invalid image URI uri=%r trace_id=%r: %s",
                    request.image_uri,
                    request.trace_id,
                    exc,
                )
                raise _image_error(422, INVALID_IMAGE_URI, exc) from exc
            detections, inference_ms = await asyncio.to_thread(
                functools.partial(
                    model.detect_image,
                    image,
                    prompt=request.prompt,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                    max_image_size=max_image_size,
                )
            )
        finally:
            _concurrency.active_requests -= 1

    return DetectResponse(
        detections=detections,
        inference_ms=round(inference_ms, 2),
        device=model.device,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
