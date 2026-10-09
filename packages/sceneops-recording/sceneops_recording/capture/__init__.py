"""Durable MCAP capture of one streamed RobotRun.

``consumer.run_capture`` consumes a run's records from the streaming transport,
writes them to an MCAP (``writer``), validates it by reading it back
(``validation``), finalizes it atomically (``finalize``) together with its capture
receipt (``receipt_io``). The atomic finalize marks the run captured; Capture
records no progress in the transport. The capture process itself is ``apps/capture``.

Requires the ``capture`` extra (``sceneops-streaming``) and, for ``message_definition``,
the ROS 2 interface definitions of the host image.
"""
