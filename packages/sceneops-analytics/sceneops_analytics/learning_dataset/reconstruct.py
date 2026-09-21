"""learning_signals.parquet rows -> AlignedSignal/LearningStep (SceneOps V2
Request 2.7B §4). Pure row->object mapping; no I/O, no Polars DataFrame
filtering (callers pass already-filtered row dicts, e.g. from
``.iter_rows(named=True)``).
"""

from __future__ import annotations

from sceneops_core.episodes.alignment import (
    AlignedSignal,
    AlignedSignalStatus,
    AlignedValue,
    AlignedValueKind,
    AssociationPolicy,
    ChannelNamespace,
    LearningStep,
)
from sceneops_core.sensors import SensorModality


def signal_from_row(row: dict) -> AlignedSignal:
    """One learning_signals.parquet row -> AlignedSignal.

    ``reference_channel``/``reference_metadata`` are not columns on
    learning_signals (LEARNING_SIGNALS_SCHEMA never persisted them, Request
    2.5 §-- see learning_tables.py) -- a reconstructed REFERENCE
    AlignedValue therefore always has those two fields unset. This is a
    known, documented lossy edge of the columnar export, not something
    reconstruction here can recover.
    """
    value = None
    if row["value_kind"] is not None:
        reference_modality = row["reference_modality"]
        value = AlignedValue(
            kind=AlignedValueKind(row["value_kind"]),
            scalar=row["value_scalar"],
            vector=row["value_vector"],
            reference_modality=(
                SensorModality(reference_modality)
                if reference_modality is not None
                else None
            ),
            reference_uri=row["reference_uri"],
        )
    return AlignedSignal(
        channel=row["channel"],
        policy=AssociationPolicy(row["policy"]),
        status=AlignedSignalStatus(row["status"]),
        value=value,
        source_timestamp_us=row["source_timestamp_us"],
        time_delta_us=row["time_delta_us"],
        source_before_timestamp_us=row["source_before_timestamp_us"],
        source_after_timestamp_us=row["source_after_timestamp_us"],
        interpolation_ratio=row["interpolation_ratio"],
    )


def step_from_rows(*, timestamp_us: int, signal_rows: list[dict]) -> LearningStep:
    """One step's timestamp + its learning_signals.parquet rows ->
    LearningStep.

    A channel absent from ``signal_rows`` never gets a dict entry -- that
    is exactly how ABSENT is preserved: no learning_signals row was ever
    written for it (the columnar builder's frozen contract, see
    learning_tables.py's module docstring). A channel present with
    status='missing' does get an entry (value=None), which is MISSING --
    the two stay distinguishable exactly as sceneops_core.episodes.learning
    (Request 2.7A) requires.
    """
    observations: dict[str, AlignedSignal] = {}
    actions: dict[str, AlignedSignal] = {}
    for row in signal_rows:
        signal = signal_from_row(row)
        if row["namespace"] == str(ChannelNamespace.OBSERVATION):
            observations[row["channel"]] = signal
        elif row["namespace"] == str(ChannelNamespace.ACTION):
            actions[row["channel"]] = signal
    return LearningStep(
        timestamp_us=timestamp_us, observations=observations, actions=actions
    )


__all__ = ["signal_from_row", "step_from_rows"]
