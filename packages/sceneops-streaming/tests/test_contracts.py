from __future__ import annotations

import pytest

from sceneops_streaming import DeliveredRecord, KafkaStartPosition


def test_start_position_text_form_round_trips():
    position = KafkaStartPosition("sceneops.robot.telemetry.v1", 2, 18432)

    assert str(position) == "sceneops.robot.telemetry.v1:2:18432"
    assert KafkaStartPosition.parse(str(position)) == position


@pytest.mark.parametrize(
    "text", ["", "topic", "topic:1", "topic:x:3", "topic:1:y", "topic:-1:3", ":1:3"]
)
def test_start_position_parse_rejects_malformed_text(text):
    with pytest.raises(ValueError):
        KafkaStartPosition.parse(text)


def test_delivered_record_is_a_start_position_only_by_conversion():
    delivered = DeliveredRecord("t", 1, 7)

    assert delivered.as_start_position() == KafkaStartPosition("t", 1, 7)
    assert not isinstance(delivered, KafkaStartPosition)
