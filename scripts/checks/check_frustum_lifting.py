"""Frustum-LiDAR 3D lifting smoke test.

GroundingDINO 없이 frustum_lifting 모듈만 단독으로 검증한다.
실제 nuScenes 샘플 + LiDAR bin 파일을 사용해 좌표 변환과
클러스터링 결과를 GT annotation과 비교한다.

Usage:
    uv run python scripts/checks/check_frustum_lifting.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent.parent
RAW_ROOT = str(ROOT / "data/raw/nuscenes")

# scene-0061-sample-0037: CAM_FRONT에 6.5m 거리 차량이 있는 샘플
SAMPLE_JSON = ROOT / "data/datasets/nuscenes/versions/v1.0-mini/samples/scene-0061-sample-0037.json"
GT_GLOBAL = [428.504, 1106.56, 1.188]   # GT annotation global translation


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from sceneops_core.datasets.schemas import DatasetSampleManifest
    from sceneops_worker.inference.detection.frustum_lifting import frustum_lift

    data = json.loads(SAMPLE_JSON.read_text())
    sample = DatasetSampleManifest.model_validate(data)
    cam = sample.sensors["CAM_FRONT"]
    lidar = sample.sensors["LIDAR_TOP"]

    print(f"Sample : {sample.sample_id}")
    print(f"LiDAR  : {lidar.filename}")
    print(f"GT car : {GT_GLOBAL}  (6.5m ahead in CAM_FRONT)")
    print()

    # bbox_2d는 800px 기준 — 카메라 좌측 가장자리의 6.5m 차량
    test_cases = [
        {
            "label": "vehicle.car @ 6.5m (GT-verified)",
            "bbox": [0.0, 180.0, 150.0, 380.0],
            "gt": GT_GLOBAL,
        },
        {
            "label": "sky region (no LiDAR → None expected)",
            "bbox": [100.0, 0.0, 400.0, 80.0],
            "gt": None,
        },
    ]

    for tc in test_cases:
        result = frustum_lift(
            bbox_2d=tc["bbox"],
            camera_sensor=cam,
            lidar_sensor=lidar,
            raw_root=RAW_ROOT,
            max_image_size=800,
        )
        label = tc["label"]
        if result is None:
            print(f"[{label}]")
            print("  → None (too few frustum points)\n")
            continue

        tx = result["translation"]
        sz = result["size"]
        print(f"[{label}]")
        print(f"  translation = {[round(v, 3) for v in tx]}")
        print(f"  size        = {[round(v, 3) for v in sz]}")
        print(f"  rotation    = {[round(v, 3) for v in result['rotation']]}")
        print(f"  points      = {result['cluster_point_count']} cluster"
              f" / {result['frustum_point_count']} frustum")
        print(f"  method      = {result['lifting_method']}")

        if tc["gt"] is not None:
            err = np.linalg.norm(np.array(tx) - np.array(tc["gt"]))
            print(f"  GT          = {tc['gt']}")
            print(f"  3D error    = {err:.3f} m  "
                  f"({'✓ within 2m threshold' if err <= 2.0 else '△ above 2m threshold'})")
        print()


if __name__ == "__main__":
    main()
