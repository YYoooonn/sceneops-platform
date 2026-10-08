"""``publish-pending`` with a fault at an exact point of a publication.

The production command (``sceneops_publisher.cli.main``: same
argument parsing, same ``publish_pending``, same ``publish_from_capture``, same
S3 store) run in a process of its own, with one addition: the ArtifactStore it
is given is wrapped so a test can kill or fail it between the two writes of a
publication. It is the Publisher counterpart of ``recovery_worker`` and reads the
same JSON control file on every write, so a test changes faults without
restarting anything::

    {"kill_after_recording": [run_id, ...],   # the process dies (os._exit) right
                                              # after the recording is stored and
                                              # before the manifest marker (W5)
     "fail_manifest_write":  [run_id, ...]}   # the manifest write raises OSError

``os._exit`` is the in-process equivalent of SIGKILL: no ``finally``, no flush,
no exit handler runs, which is what a killed publisher looks like to the store.

Run it exactly like the command::

    python -m recovery_publisher publish-pending --capture-root <root>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sceneops_publisher import cli as _cli

_CONTROL = Path(os.environ["RECOVERY_FAULT_FILE"])
KILL_EXIT_CODE = 137

_RECORDING = "/recording.mcap"
_MANIFEST = "/robot_run_manifest.json"


def _run_id(uri: str) -> str:
    return uri.rstrip("/").split("/")[-2]


def _faults(run_id: str) -> set[str]:
    try:
        control = json.loads(_CONTROL.read_text())
    except FileNotFoundError:
        return set()
    return {fault for fault, run_ids in control.items() if run_id in run_ids}


class _FaultyStore:
    """Delegates everything to the real store except ``write_bytes``."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def write_bytes(self, uri, data):
        faults = _faults(_run_id(uri))
        if uri.endswith(_MANIFEST) and "fail_manifest_write" in faults:
            raise OSError("injected manifest write failure")
        await self._inner.write_bytes(uri, data)
        if uri.endswith(_RECORDING) and "kill_after_recording" in faults:
            os._exit(KILL_EXIT_CODE)


_real_create_artifact_store = _cli.create_artifact_store
_cli.create_artifact_store = lambda settings: _FaultyStore(
    _real_create_artifact_store(settings)
)

if __name__ == "__main__":
    sys.exit(_cli.main())
