"""Unit tests for KafkaTelemetryConsumer's manual-commit support. No real
broker -- confluent_kafka.Consumer is monkeypatched with a minimal fake
that records constructor config and commit() calls.
"""

from __future__ import annotations

import pytest

from sceneops_streaming import consumer as consumer_module
from sceneops_streaming.config import StreamingSettings
from sceneops_streaming.consumer import KafkaTelemetryConsumer


class _FakeConfluentConsumer:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.subscribed_topics: list[str] = []
        self.committed: list[tuple[object, bool]] = []

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics = topics

    def poll(self, timeout_seconds: float):
        return None

    def commit(self, message=None, asynchronous=True) -> None:
        self.committed.append((message, asynchronous))

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_confluent_consumer(monkeypatch):
    monkeypatch.setattr(consumer_module, "ConfluentConsumer", _FakeConfluentConsumer)


def test_enable_auto_commit_defaults_true_unchanged_for_existing_callers() -> None:
    consumer = KafkaTelemetryConsumer(settings=StreamingSettings(_env_file=None))
    assert consumer._consumer.config["enable.auto.commit"] is True


def test_enable_auto_commit_false_is_passed_through() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    assert consumer._consumer.config["enable.auto.commit"] is False


async def test_commit_with_no_polled_message_is_a_no_op() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    await consumer.commit()
    assert consumer._consumer.committed == []


async def test_commit_commits_the_last_polled_message_synchronously() -> None:
    consumer = KafkaTelemetryConsumer(
        settings=StreamingSettings(_env_file=None), enable_auto_commit=False
    )
    sentinel_message = object()
    consumer._last_message = sentinel_message

    await consumer.commit()

    assert consumer._consumer.committed == [(sentinel_message, False)]
