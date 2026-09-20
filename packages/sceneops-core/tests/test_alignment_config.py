"""Tests for TemporalAlignmentConfig / TemporalSourceContext validation and
canonicalization (SceneOps V2 Request 2.2 §5, §6, §47)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.episodes.alignment import (
    MCAP_LOG_TIME_CLOCK,
    AssociationPolicy,
    ChannelPolicyConfig,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    canonical_config_dict,
)


class TestTemporalSourceContext:
    def test_requires_non_empty_source_clock(self) -> None:
        with pytest.raises(ValidationError):
            TemporalSourceContext(source_clock="")

    def test_accepts_known_mcap_clock(self) -> None:
        ctx = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)
        assert ctx.source_clock == "mcap_log_time"


class TestTemporalAlignmentConfigValidation:
    def test_target_frequency_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TemporalAlignmentConfig(target_frequency_hz=0)
        with pytest.raises(ValidationError):
            TemporalAlignmentConfig(target_frequency_hz=-1.0)

    def test_does_not_derive_frequency_from_anything(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0)
        assert config.target_frequency_hz == 10.0

    def test_explicit_nearest_override_without_any_tolerance_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemporalAlignmentConfig(
                target_frequency_hz=10.0,
                channel_policies={
                    "state.velocity": ChannelPolicyConfig(
                        policy=AssociationPolicy.NEAREST
                    )
                },
            )

    def test_explicit_nearest_override_covered_by_global_tolerance_is_accepted(
        self,
    ) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            tolerance_us=50_000,
            channel_policies={
                "state.velocity": ChannelPolicyConfig(policy=AssociationPolicy.NEAREST)
            },
        )
        assert (
            config.channel_policies["state.velocity"].policy
            == AssociationPolicy.NEAREST
        )

    def test_explicit_interpolation_override_without_any_max_gap_is_rejected(
        self,
    ) -> None:
        with pytest.raises(ValidationError):
            TemporalAlignmentConfig(
                target_frequency_hz=10.0,
                channel_policies={
                    "state.position": ChannelPolicyConfig(
                        policy=AssociationPolicy.LINEAR_INTERPOLATION
                    )
                },
            )

    def test_explicit_interpolation_override_with_own_max_gap_is_accepted(self) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            channel_policies={
                "state.position": ChannelPolicyConfig(
                    policy=AssociationPolicy.LINEAR_INTERPOLATION, max_gap_us=200_000
                )
            },
        )
        assert config.channel_policies["state.position"].max_gap_us == 200_000


class TestConfigCanonicalization:
    def test_omitted_optional_and_explicit_none_serialize_identically(self) -> None:
        omitted = TemporalAlignmentConfig(target_frequency_hz=10.0)
        explicit_none = TemporalAlignmentConfig(
            target_frequency_hz=10.0, tolerance_us=None, max_gap_us=None
        )
        assert canonical_config_dict(omitted) == canonical_config_dict(explicit_none)
        assert "tolerance_us" not in canonical_config_dict(omitted)
        assert "max_gap_us" not in canonical_config_dict(omitted)

    def test_channel_override_insertion_order_does_not_change_canonical_meaning(
        self,
    ) -> None:
        config_a = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            tolerance_us=1_000,
            channel_policies={
                "state.velocity": ChannelPolicyConfig(policy=AssociationPolicy.NEAREST),
                "steering": ChannelPolicyConfig(policy=AssociationPolicy.PREVIOUS),
            },
        )
        config_b = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            tolerance_us=1_000,
            channel_policies={
                "steering": ChannelPolicyConfig(policy=AssociationPolicy.PREVIOUS),
                "state.velocity": ChannelPolicyConfig(policy=AssociationPolicy.NEAREST),
            },
        )
        dict_a = canonical_config_dict(config_a)
        dict_b = canonical_config_dict(config_b)
        assert dict_a == dict_b
        # Prove the eventual sort_keys=True execution-key hashing step (Request
        # 2.3, not computed here) would also see identical JSON regardless of
        # insertion order.
        assert json.dumps(dict_a, sort_keys=True) == json.dumps(dict_b, sort_keys=True)

    def test_semantically_different_configs_canonicalize_differently(self) -> None:
        a = canonical_config_dict(TemporalAlignmentConfig(target_frequency_hz=10.0))
        b = canonical_config_dict(TemporalAlignmentConfig(target_frequency_hz=20.0))
        assert a != b
