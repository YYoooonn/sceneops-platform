"""Smoke test for the inference server GroundingDINO backend.

두 가지 모드로 실행 가능:
  1. 로컬 직접 실행 (inference-server 패키지 직접 임포트):
       uv run python scripts/checks/check_grounding_dino.py [IMAGE_PATH]

  2. HTTP 클라이언트 모드 (inference server가 떠 있을 때):
       uv run python scripts/checks/check_grounding_dino.py --http http://localhost:8001 [IMAGE_PATH]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _find_default_image() -> Path:
    cam_front = ROOT / "data/raw/nuscenes/samples/CAM_FRONT"
    jpegs = sorted(cam_front.glob("*.jpg"))
    if not jpegs:
        print(f"No CAM_FRONT images found under {cam_front}")
        sys.exit(1)
    return jpegs[0]


def run_local(image_path: Path) -> None:
    """직접 모델 로드 후 추론 (inference-server 패키지 사용)."""
    from inference_server.grounding_dino import GroundingDinoModel
    from inference_server.schemas import DetectRequest

    print(f"[local] Image: {image_path}")
    print("Loading GroundingDINO-T model...")

    model = GroundingDinoModel()
    model.load()
    print(f"Device: {model.device}\n")

    req = DetectRequest(image_path=str(image_path))
    detections, inference_ms = model.detect(req)

    print(f"Inference: {inference_ms:.1f} ms")
    print(f"Detections ({len(detections)}):")
    for det in detections:
        print(f"  {det.category_name:40s}  score={det.score:.3f}  bbox={det.bbox_2d}")


def run_http(endpoint_url: str, image_path: Path) -> None:
    """HTTP 클라이언트 모드 — inference server에 요청."""
    import httpx

    print(f"[http] Endpoint: {endpoint_url}")
    print(f"[http] Image:    {image_path}\n")

    response = httpx.get(f"{endpoint_url}/healthz")
    print(f"Health: {response.json()}\n")

    response = httpx.post(
        f"{endpoint_url}/v1/detect",
        json={"image_path": str(image_path)},
        timeout=120.0,
    )
    response.raise_for_status()
    data = response.json()

    print(f"Inference: {data['inference_ms']:.1f} ms  device={data['device']}")
    print(f"Detections ({len(data['detections'])}):")
    for det in data["detections"]:
        print(f"  {det['category_name']:40s}  score={det['score']:.3f}  bbox={det['bbox_2d']}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--http" in args:
        idx = args.index("--http")
        endpoint = args[idx + 1]
        remaining = args[:idx] + args[idx + 2:]
        img = Path(remaining[0]) if remaining else _find_default_image()
        run_http(endpoint, img)
    else:
        img = Path(args[0]) if args else _find_default_image()
        if not img.exists():
            print(f"File not found: {img}")
            sys.exit(1)
        run_local(img)
