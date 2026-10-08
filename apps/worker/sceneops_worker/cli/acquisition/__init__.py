"""``sceneops-worker acquisition ...``: the one-shot acquisition commands (ADR-008).

Each command is the unchanged argparse entrypoint of its module; typer only routes to
it and hands over the remaining arguments, so the flags are the commands' own.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from sceneops_worker.cli.acquisition import artifact_lifecycle, reconcile, status

app = typer.Typer(
    name="acquisition",
    help="One-shot acquisition commands: reconcile, status, artifact-lifecycle.",
    no_args_is_help=True,
)

_PASS_THROUGH = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": [],
}


def _command(main: Callable[[list[str] | None], int]):
    def run(ctx: typer.Context) -> None:
        raise typer.Exit(main(list(ctx.args)))

    return run


app.command(
    "reconcile",
    context_settings=_PASS_THROUGH,
    help="Classify every run; --apply performs the bounded registration recovery.",
)(_command(reconcile.main))
app.command(
    "status",
    context_settings=_PASS_THROUGH,
    help="Read-only operational report: a derived AcquisitionStatus per run.",
)(_command(status.main))
app.command(
    "artifact-lifecycle",
    context_settings=_PASS_THROUGH,
    help="Read-only lifecycle classification of the RobotRun root's objects.",
)(_command(artifact_lifecycle.main))
