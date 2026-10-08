"""The capture suite runs only where the ROS 2 interface definitions exist.

Capture writes MCAP schemas from the ``.msg`` files of the ROS 2 distribution
(``sceneops_recording.capture.message_definition``), and its integration tests use a
real Kafka broker: the suite runs inside the ROS 2 capture image (``make streaming-test``),
not in the host environment of ``make test``.
"""

try:
    import ament_index_python  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_*.py"]
