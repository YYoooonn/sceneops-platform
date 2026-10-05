"""The Airflow DAG file cannot import SceneOps, so it mirrors the pipeline
definitions' task ids statically. This keeps the mirror honest."""

from __future__ import annotations

import ast
from pathlib import Path

from sceneops_core.pipelines.builtin import BUILTIN_PIPELINE_DEFINITIONS

_DAG_FILE = Path(__file__).parents[4] / "airflow" / "dags" / "sceneops_pipelines.py"


def _mirror() -> dict[str, list[str]]:
    tree = ast.parse(_DAG_FILE.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "PIPELINE_TASK_IDS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("PIPELINE_TASK_IDS not found in the DAG file")


def test_the_dag_file_has_one_dag_per_final_pipeline_with_its_task_order() -> None:
    expected = {
        d.type.value: [
            t.pipeline_task_id for t in sorted(d.tasks, key=lambda t: t.order)
        ]
        for d in BUILTIN_PIPELINE_DEFINITIONS
    }
    assert _mirror() == expected
