# Phase 6.1 streaming transport foundation. This is the single
# authoritative source for the default telemetry topic name -- compose,
# settings defaults, and docs all point back here rather than each
# hardcoding their own copy of the literal.
DEFAULT_TELEMETRY_TOPIC = "sceneops.robot.telemetry.v1"

# Kafka header key prefix used by the wire mapping in sceneops-streaming
# (sceneops_streaming.wire). Defined here, not there, so the logical
# envelope contract and its concrete Kafka header names stay
# cross-referenced from one place even though sceneops-core itself never
# imports a Kafka client.
TELEMETRY_HEADER_PREFIX = "sceneops.envelope."
