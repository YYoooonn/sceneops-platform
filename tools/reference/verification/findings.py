"""Violations collected by every verification layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Findings:
    """Violations of one verification, each with a stable code.

    A negative control names the code its fault must produce, so codes are
    part of the verifier's contract (docs/development/reference-verification.md).
    """

    context: dict[str, Any] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    def scoped(self, **context: Any) -> Findings:
        child = Findings({**self.context, **context}, self.violations, self.checked)
        return child

    def check(self, condition: bool, code: str, message: str) -> bool:
        self.checked[code] = self.checked.get(code, 0) + 1
        if not condition:
            self.violations.append({"code": code, **self.context, "message": message})
        return condition

    def equal(self, observed: Any, expected: Any, code: str, what: str) -> bool:
        return self.check(
            observed == expected,
            code,
            f"{what}: observed {_short(observed)}, expected {_short(expected)}",
        )

    def fail(self, code: str, message: str) -> None:
        self.check(False, code, message)

    @property
    def ok(self) -> bool:
        return not self.violations

    def codes(self) -> set[str]:
        return {v["code"] for v in self.violations}


def _short(value: Any, limit: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
