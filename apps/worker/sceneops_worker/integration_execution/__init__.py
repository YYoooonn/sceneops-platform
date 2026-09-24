"""Generic ``IntegrationRequest`` -> ``IntegrationResult`` execution
(SceneOps V2 Request 4.6/4.6A): HOW an already-built request is run against
some isolated integration runtime, never WHAT that runtime does or WHY
it's being run. See ``executor.py`` for the full architectural split.

Two ``IntegrationExecutor`` backends:

* ``HttpIntegrationExecutor`` (``http.py``) -- the production backend
  (Request 4.6A): calls an integration runtime's ``POST /execute`` HTTP
  endpoint over the SceneOps internal network.
* ``ContainerIntegrationExecutor`` (``container.py``) -- a local/dev
  backend (Request 4.6, demoted in 4.6A): spawns the runtime as a sibling
  Docker container via the host's Docker daemon. No longer the worker's
  normal production path (that required a `docker` CLI + `/var/run/
  docker.sock` mount this worker no longer has) -- still used by
  ``make nuscenes-container-smoke`` and direct local runtime testing.
"""

from .container import (
    ContainerIntegrationExecutor,
    ContainerRuntimeConfig,
    artifact_settings_to_env,
)
from .errors import IntegrationExecutionError
from .executor import IntegrationExecutor
from .http import HttpIntegrationExecutor, HttpRuntimeConfig
from .in_process import InProcessIntegrationExecutor

__all__ = [
    "ContainerIntegrationExecutor",
    "ContainerRuntimeConfig",
    "HttpIntegrationExecutor",
    "HttpRuntimeConfig",
    "IntegrationExecutionError",
    "IntegrationExecutor",
    "InProcessIntegrationExecutor",
    "artifact_settings_to_env",
]
