from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import DEFAULT_TELEMETRY_TOPIC


class StreamingSettings(BaseSettings):
    """SceneOps streaming-CLIENT configuration -- how SceneOps
    applications reach Kafka. Deliberately distinct from the Kafka
    BROKER's own configuration (KRaft node id, listeners, controller
    quorum, ...), which lives entirely inside ``compose/streaming.yaml``
    and is never exposed as a ``SCENEOPS_STREAMING_KAFKA_*`` variable --
    those are infrastructure implementation details, not something a
    SceneOps application ever needs to read.

    ``SCENEOPS_STREAMING_KAFKA_`` env prefix -- four variables, the
    complete public surface:

        SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS
        SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC
        SCENEOPS_STREAMING_KAFKA_PRODUCER_CLIENT_ID
        SCENEOPS_STREAMING_KAFKA_CONSUMER_GROUP_ID

    Deliberately NOT exposed as environment variables: ``acks``,
    ``retries``, ``linger.ms``, ``batch.size``, compression,
    ``auto.offset.reset``, ``enable.auto.commit``, session timeouts --
    these stay as explicit code-level defaults in ``producer.py``/
    ``consumer.py`` until a real override need exists.

    Precedence (pydantic-settings' own default behavior, not a custom
    mechanism): explicit constructor kwarg > environment variable >
    ``.env.local``/``.env`` file > the field default shown below. See
    ``tests/test_config.py`` for a test proving this ordering.

    No top-level ``BaseSettings`` for this lives in ``sceneops-core``
    (unlike ``ArtifactSettings``/``ExecutionSettings``, which are composed
    into each app's own ``WorkerSettings``/``ApiSettings``) -- this
    package remains the only thing that constructs a Kafka client. The
    ROS2 streaming bridge constructs ``StreamingSettings()`` directly
    rather than embedding it into an app-level settings wrapper.
    """

    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_STREAMING_KAFKA_",
        extra="ignore",
    )

    bootstrap_servers: str = "kafka:9092"
    telemetry_topic: str = DEFAULT_TELEMETRY_TOPIC
    producer_client_id: str = "sceneops-telemetry-producer"

    # The application-level default consumer group -- a caller that
    # doesn't need per-instance isolation (e.g. the ROS2 bridge, running
    # as a single long-lived consumer) uses this verbatim. A caller that
    # DOES need isolation (make smoke-streaming, so repeated runs don't
    # inherit a previous run's committed offsets) derives its own group
    # id FROM this configured base rather than inventing an unrelated
    # literal -- see tools/e2e/smoke_streaming.py.
    consumer_group_id: str = "sceneops-telemetry-consumer"


@lru_cache
def get_settings() -> StreamingSettings:
    return StreamingSettings()
