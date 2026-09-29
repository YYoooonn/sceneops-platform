"""Tests for StreamingSettings (Phase 6.1 follow-up request §15) --
defaults, the SCENEOPS_STREAMING_KAFKA_ env-var surface, and precedence
(explicit kwarg > env var > field default; pydantic-settings' own
built-in behavior, not a custom mechanism). No Kafka broker involved.
"""

from __future__ import annotations

from sceneops_streaming.config import StreamingSettings


def test_defaults_match_documented_public_surface() -> None:
    settings = StreamingSettings(_env_file=None)

    assert settings.bootstrap_servers == "kafka:9092"
    assert settings.telemetry_topic == "sceneops.robot.telemetry.v1"
    assert settings.producer_client_id == "sceneops-telemetry-producer"
    assert settings.consumer_group_id == "sceneops-telemetry-consumer"


def test_env_var_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv(
        "SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC", "sceneops.robot.telemetry.v2"
    )
    monkeypatch.setenv("SCENEOPS_STREAMING_KAFKA_PRODUCER_CLIENT_ID", "custom-producer")
    monkeypatch.setenv("SCENEOPS_STREAMING_KAFKA_CONSUMER_GROUP_ID", "custom-consumer")

    settings = StreamingSettings(_env_file=None)

    assert settings.bootstrap_servers == "localhost:9092"
    assert settings.telemetry_topic == "sceneops.robot.telemetry.v2"
    assert settings.producer_client_id == "custom-producer"
    assert settings.consumer_group_id == "custom-consumer"


def test_explicit_constructor_kwarg_overrides_env_var(monkeypatch) -> None:
    monkeypatch.setenv("SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS", "from-env:9092")

    settings = StreamingSettings(_env_file=None, bootstrap_servers="from-kwarg:9092")

    assert settings.bootstrap_servers == "from-kwarg:9092"


def test_unrelated_env_prefix_is_ignored(monkeypatch) -> None:
    # Old Phase 6.1 naming (no _KAFKA_ infix) must not leak in -- proves
    # the env_prefix rename is real, not just a docstring claim.
    monkeypatch.setenv("SCENEOPS_STREAMING_BOOTSTRAP_SERVERS", "should-not-apply:9092")

    settings = StreamingSettings(_env_file=None)

    assert settings.bootstrap_servers == "kafka:9092"
