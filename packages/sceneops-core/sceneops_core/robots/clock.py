"""Recording clock identifiers for L1 robot recordings (ADR-007 §29.5 R5).

``MCAP_LOG_TIME_CLOCK`` names the clock MCAP ``Message.log_time`` is written
in: the recorder's receive time. It is the value of
``RobotRunManifest.capture.source_clock`` for every v1 recording and the
clock ``started_at`` / ``ended_at`` are derived from. It makes no claim about
the observation time a source message carries in its own payload.

A plain constant, not a closed enum: clock identifiers are open (I-28), and
the publisher keeps its own supported set.
"""

from __future__ import annotations

from typing import Final

MCAP_LOG_TIME_CLOCK: Final = "mcap_log_time"

__all__ = ["MCAP_LOG_TIME_CLOCK"]
