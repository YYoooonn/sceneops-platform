"""Semantic acquisition equivalence of two recordings (ADR-007 §29.12)."""

from __future__ import annotations

import json
from pathlib import Path

from mcap.writer import Writer

from sceneops_integrations.recording import compare_recordings
from sceneops_integrations.recording.cli import main as cli_main


def write(
    path: Path,
    messages: list[tuple[str, bytes, int, int | None]],
    *,
    schema_text: str = "string data\n",
    message_type: str = "std_msgs/msg/String",
) -> Path:
    """messages: (topic, payload, log_time, sequence)."""
    with path.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="ros2", library="test")
        schema = writer.register_schema(
            name=message_type, encoding="ros2msg", data=schema_text.encode()
        )
        channels: dict[str, int] = {}
        for topic, payload, log_time, sequence in messages:
            if topic not in channels:
                channels[topic] = writer.register_channel(
                    topic=topic, message_encoding="cdr", schema_id=schema
                )
            writer.add_message(
                channel_id=channels[topic],
                log_time=log_time,
                publish_time=log_time,
                data=payload,
                sequence=sequence or 0,
            )
        writer.finish()
    return path


def test_equal_content_with_different_timing_order_and_sequence_values(
    tmp_path: Path,
) -> None:
    batch = write(
        tmp_path / "a.mcap",
        [("/a", b"1", 10, 1), ("/b", b"x", 10, 1), ("/a", b"2", 20, 2)],
    )
    stream = write(
        tmp_path / "b.mcap",
        # other channel first, receive times unrelated, global sequence values
        [("/b", b"x", 900, 1), ("/a", b"1", 905, 2), ("/a", b"2", 990, 7)],
        schema_text="string   data # reformatted\n",
    )

    report = compare_recordings(batch, stream)

    assert report.equivalent, report.differences
    assert (report.channel_count, report.message_count) == (2, 3)


def test_duplicates_are_counted_not_collapsed(tmp_path: Path) -> None:
    once = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1)])
    twice = write(tmp_path / "b.mcap", [("/a", b"1", 1, 1), ("/a", b"1", 2, 2)])

    report = compare_recordings(once, twice)

    assert not report.equivalent
    assert "message multisets differ" in report.differences[0]


def test_changed_payload_is_a_difference(tmp_path: Path) -> None:
    a = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1)])
    b = write(tmp_path / "b.mcap", [("/a", b"2", 1, 1)])

    assert not compare_recordings(a, b).equivalent


def test_channel_only_in_one_recording_is_reported(tmp_path: Path) -> None:
    a = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1), ("/b", b"1", 1, 1)])
    b = write(tmp_path / "b.mcap", [("/a", b"1", 1, 1)])

    [difference] = compare_recordings(a, b).differences
    assert "'/b' only in the first" in difference


def test_per_channel_sequence_order_must_match_when_both_are_sequenced(
    tmp_path: Path,
) -> None:
    a = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1), ("/a", b"2", 2, 2)])
    swapped = write(tmp_path / "b.mcap", [("/a", b"1", 1, 2), ("/a", b"2", 2, 1)])
    unsequenced = write(
        tmp_path / "c.mcap", [("/a", b"2", 1, None), ("/a", b"1", 2, None)]
    )

    assert not compare_recordings(a, swapped).equivalent
    # Order is compared only where both recordings carry sequences.
    assert compare_recordings(a, unsequenced).equivalent


def test_type_difference_is_reported(tmp_path: Path) -> None:
    a = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1)])
    b = write(
        tmp_path / "b.mcap", [("/a", b"1", 1, 1)], message_type="std_msgs/msg/Other"
    )

    assert not compare_recordings(a, b).equivalent


def test_cli_compare_exit_status(tmp_path: Path, capsys) -> None:
    a = write(tmp_path / "a.mcap", [("/a", b"1", 1, 1)])
    same = write(tmp_path / "b.mcap", [("/a", b"1", 5, 3)])
    other = write(tmp_path / "c.mcap", [("/a", b"9", 5, 3)])

    assert cli_main(["compare", "--first", str(a), "--second", str(same)]) == 0
    assert json.loads(capsys.readouterr().out)["equivalent"] is True
    assert cli_main(["compare", "--first", str(a), "--second", str(other)]) == 1


def test_contents_compare_without_files_so_perturbations_are_detected(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from collections import Counter

    from sceneops_integrations.recording import (
        compare_recording_contents,
        semantic_recording_content,
    )

    path = write(tmp_path / "a.mcap", [("/a", b"1", 10, 1), ("/a", b"2", 20, 2)])
    content = semantic_recording_content(path)
    assert compare_recording_contents(content, content).equivalent

    channel = content.channels["/a"]
    dropped = replace(
        content,
        channels={
            "/a": replace(
                channel,
                message_count=1,
                payloads=Counter(list(channel.payloads.elements())[:1]),
            )
        },
    )
    report = compare_recording_contents(content, dropped)
    assert not report.equivalent
    assert "message multisets differ" in report.differences[0]
