"""How a recorded message becomes a canonical observation payload (ADR-007
§30.4).

Shared by every recording builder: a payload is a property of one recorded
message and one extraction, not of the domain that references it, so a Scene
and an Episode built from the same RobotRun reference the same payload
artifact when they extract the same message the same way.
"""

from __future__ import annotations

from enum import StrEnum


class PayloadExtraction(StrEnum):
    """``compressed_image``  the ``data`` bytes of a ``sensor_msgs/msg/CompressedImage``,
                             unchanged; ``image/jpeg`` or ``image/png`` from its
                             ``format``
    ``ros2_message``         the recorded message bytes exactly as serialized
                             (CDR, encapsulation header included); the media type
                             names the ROS 2 message type
    """

    COMPRESSED_IMAGE = "compressed_image"
    ROS2_MESSAGE = "ros2_message"


__all__ = ["PayloadExtraction"]
