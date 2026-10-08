#!/bin/sh
# Run a one-shot command forever: invoke, sleep, repeat.
#
#   poll_loop.sh <interval-seconds> <command> [args...]
#
# This is deliberately the whole of the "scheduler" (ADR-008 §3.2): the
# command is a stateless one-shot (`reconcile --once --apply`, `publish-pending`)
# that re-derives everything from durable facts, so this loop holds no state,
# adds no behavior, and killing or restarting it loses nothing. A failing
# invocation is logged and retried on the next interval; it never stops the loop.
set -u

interval="$1"
shift

trap 'exit 0' TERM INT

while true; do
    "$@"
    status=$?
    if [ "$status" -ne 0 ]; then
        echo "poll_loop: '$1' exited with status $status; retrying in ${interval}s" >&2
    fi
    # `sleep` in the background so TERM interrupts the wait immediately.
    sleep "$interval" &
    wait $!
done
