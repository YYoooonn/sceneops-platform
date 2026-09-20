from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import AssociationPolicy, TimelineMode

# The only source clock the current MCAP-backed pipeline produces (SceneOps
# V2 Request 2.1B §1/§2) — a convenience constant, not a closed enum, since
# TemporalSourceContext.source_clock must stay open to future producers.
MCAP_LOG_TIME_CLOCK = "mcap_log_time"


class TemporalSourceContext(SceneOpsBaseModel):
    """What temporal source is being aligned — deliberately separate from
    TemporalAlignmentConfig (*how* alignment is performed). The pure engine
    never infers or hardcodes this; callers must always pass it explicitly
    (SceneOps V2 Request 2.1B §2, Request 2.2 §4)."""

    source_clock: str = Field(min_length=1)


class ChannelPolicyConfig(SceneOpsBaseModel):
    """Explicit per-channel override, applied on top of the semantic
    defaults (Request 2.1B §9) during effective-policy resolution
    (Request 2.2 §20/§21). Any field left unset falls through to the
    channel's semantic default / the config's global tolerance_us /
    max_gap_us."""

    policy: AssociationPolicy | None = None
    tolerance_us: int | None = None
    max_gap_us: int | None = None


class TemporalAlignmentConfig(SceneOpsBaseModel):
    """How alignment is performed — v1 semantics only (SceneOps V2 Request
    2.1B, Request 2.2). ``target_frequency_hz`` is always explicit; it is
    never derived from EpisodeManifest.control_frequency_hz."""

    timeline_mode: TimelineMode = TimelineMode.FIXED_FREQUENCY
    target_frequency_hz: float = Field(gt=0)

    tolerance_us: int | None = None
    max_gap_us: int | None = None

    channel_policies: dict[str, ChannelPolicyConfig] = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_explicit_overrides(self) -> TemporalAlignmentConfig:
        """Config-self-contained validation only — catches an explicit
        override that requests nearest/linear_interpolation with no
        tolerance/max_gap anywhere in reach. Default-derived policies (which
        depend on the manifest's actual channels) are re-validated at
        resolution time in policies.resolve_channel_policy; this is a
        best-effort early check, not the sole authority."""
        # pylint: disable=no-member
        for channel, override in self.channel_policies.items():
            if override.policy == AssociationPolicy.NEAREST:
                effective_tolerance = (
                    override.tolerance_us
                    if override.tolerance_us is not None
                    else self.tolerance_us
                )
                if effective_tolerance is None:
                    raise ValueError(
                        f"channel_policies[{channel!r}] sets policy=nearest but no "
                        "tolerance_us is available (override or global)"
                    )
            if override.policy == AssociationPolicy.LINEAR_INTERPOLATION:
                effective_max_gap = (
                    override.max_gap_us
                    if override.max_gap_us is not None
                    else self.max_gap_us
                )
                if effective_max_gap is None:
                    raise ValueError(
                        f"channel_policies[{channel!r}] sets "
                        "policy=linear_interpolation but no max_gap_us is available "
                        "(override or global)"
                    )
        return self


def canonical_config_dict(config: TemporalAlignmentConfig) -> dict[str, Any]:
    """Deterministic canonical representation for future Request 2.3
    execution-key hashing (SceneOps V2 Request 2.1B §17, Request 2.2 §6).

    ``exclude_none=True`` is the load-bearing choice: it strips any field
    whose *current value* is None regardless of whether it was passed
    explicitly or left at its default, so "omitted optional" and "explicit
    None" always canonicalize identically. Dict key order (top-level and
    within channel_policies) is irrelevant here — sort_keys=True at the
    eventual compute_execution_key() call handles that; this request does
    not compute the execution key itself.
    """
    return config.model_dump(mode="json", exclude_none=True)


def alignment_config_hash(config: TemporalAlignmentConfig) -> str:
    """Deterministic sha256 over the config's canonical representation
    (SceneOps V2 Request 2.3 §15) -- reuses canonical_config_dict() rather
    than duplicating serialization/rounding semantics. Same config
    (regardless of channel_policies insertion order or omitted-vs-explicit-
    None) always hashes identically; any semantic difference changes it."""
    canonical = json.dumps(
        canonical_config_dict(config), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
