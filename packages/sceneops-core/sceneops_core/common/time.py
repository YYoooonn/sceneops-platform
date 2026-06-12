from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def utc_now() -> datetime:
    return datetime.now(UTC)
