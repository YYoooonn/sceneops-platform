"""Container-level I-36 check for the dataset-acquisition image.

Run inside the image (no SceneOps environment needed on the host):

    docker compose --env-file .env.local --profile acquisition run --rm -T \\
        --entrypoint python dataset-acquisition - < scripts/checks/acquisition_image_boundary.py

Fails unless the image's environment contains no SceneOps distribution other
than the tool itself, cannot import any SceneOps package, and loads none
when the tool's modules are imported.
"""

import importlib.metadata
import importlib.util
import sys

SELF = "sceneops-dataset-acquisition"
PLATFORM_MODULES = (
    "sceneops_core",
    "sceneops_db",
    "sceneops_storage",
    "sceneops_streaming",
    "sceneops_integrations",
    "sceneops_analytics",
    "sceneops_worker",
    "sceneops_api",
    "app",
)

distributions = sorted(
    {(d.metadata["Name"] or "").lower() for d in importlib.metadata.distributions()}
)
foreign = [d for d in distributions if d.startswith("sceneops") and d != SELF]
importable = [m for m in PLATFORM_MODULES if importlib.util.find_spec(m) is not None]

import dataset_acquisition.cli  # noqa: E402,F401
import dataset_acquisition.mcap_sink  # noqa: E402,F401
import dataset_acquisition.nuscenes  # noqa: E402,F401
import dataset_acquisition.ros2_replay  # noqa: E402,F401

loaded = sorted(m for m in sys.modules if m.split(".")[0] in PLATFORM_MODULES)

print(
    f"distributions={len(distributions)} sceneops={[d for d in distributions if d.startswith('sceneops')]}"
)
if foreign or importable or loaded:
    print(f"FAIL foreign={foreign} importable={importable} loaded={loaded}")
    sys.exit(1)
print("OK: no SceneOps package installed, importable or loaded")
