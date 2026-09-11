from __future__ import annotations

import polars as pl
import pytest

from sceneops_analytics.query import query_parquet


def test_query_parquet_runs_sql_against_a_real_file(tmp_path):
    df = pl.DataFrame(
        {
            "robot_id": ["r1", "r1", "r2"],
            "battery": [90.0, 80.0, 70.0],
        }
    )
    path = str(tmp_path / "robot_telemetry.parquet")
    df.write_parquet(path)

    result = query_parquet(
        {"robot_telemetry": path},
        "SELECT robot_id, avg(battery) AS avg_battery "
        "FROM robot_telemetry GROUP BY robot_id ORDER BY robot_id",
    )

    assert result["robot_id"].to_list() == ["r1", "r2"]
    assert result["avg_battery"].to_list() == [85.0, 70.0]


def test_query_parquet_joins_across_two_views(tmp_path):
    telemetry = pl.DataFrame({"robot_id": ["r1", "r2"], "battery": [90.0, 70.0]})
    missions = pl.DataFrame(
        {"robot_id": ["r1", "r2"], "status": ["completed", "running"]}
    )

    telemetry_path = str(tmp_path / "telemetry.parquet")
    missions_path = str(tmp_path / "missions.parquet")
    telemetry.write_parquet(telemetry_path)
    missions.write_parquet(missions_path)

    result = query_parquet(
        {"telemetry": telemetry_path, "missions": missions_path},
        "SELECT t.robot_id, t.battery, m.status FROM telemetry t "
        "JOIN missions m USING (robot_id) ORDER BY t.robot_id",
    )

    assert result["status"].to_list() == ["completed", "running"]


def test_query_parquet_rejects_invalid_view_name(tmp_path):
    path = str(tmp_path / "x.parquet")
    pl.DataFrame({"a": [1]}).write_parquet(path)

    with pytest.raises(ValueError, match="Invalid view name"):
        query_parquet({"bad; DROP TABLE x": path}, "SELECT 1")
