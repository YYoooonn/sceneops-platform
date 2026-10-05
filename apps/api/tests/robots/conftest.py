"""Doubles shared by the reconciliation tests (``test_reconciliation*.py``)."""

from __future__ import annotations

import pytest

from tests.robots.test_reconciliation import FakeFacts, MemoryStore


@pytest.fixture()
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture()
def facts() -> FakeFacts:
    return FakeFacts()
