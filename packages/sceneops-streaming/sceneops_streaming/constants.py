# Single authoritative source for the default telemetry topic name --
# compose, settings defaults, and docs all point back here rather than
# each hardcoding their own copy of the literal.
DEFAULT_TELEMETRY_TOPIC = "sceneops.robot.telemetry.v1"

# Kafka header key prefix used by the wire mapping in sceneops-streaming
# (sceneops_streaming.wire). Defined here, not there, so the logical
# envelope contract and its concrete Kafka header names stay
# cross-referenced from one place even though sceneops-core itself never
# imports a Kafka client.
TELEMETRY_HEADER_PREFIX = "sceneops.envelope."

# Reserved TelemetryEnvelope.channel value for run lifecycle control
# events (Phase 7.2 -- sceneops_streaming.control). Additive: not a
# real ROS2 topic, never subscribed to by the ROS2 bridge, never written
# to MCAP (``ChannelSpec`` rejects it, so it can never enter the channel
# registry in ``sceneops_streaming.channels``) -- a consumer
# recognizes it by this channel name alone and intercepts it before any
# telemetry-specific handling (sequence tracking, MCAP writing) ever
# sees it. Carried on the SAME Kafka topic as telemetry, under the SAME
# robot_run_id key, specifically so it orders consistently with that
# run's own telemetry within one partition -- see
# docs/history/streaming-multirun-phase7-study.md's Phase 7.2
# section for the full "why the existing topic, not a separate one"
# reasoning.
SESSION_CONTROL_CHANNEL = "/session/control"
