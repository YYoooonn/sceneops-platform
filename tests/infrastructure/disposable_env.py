"""The disposable test environment of `make test-integration` and `make test-infrastructure SUITE=recovery`.

Integration and recovery tests commit rows and objects of their own. They run
against a PostgreSQL database and a MinIO bucket that exist only for the run, so
the local reference environment is never written to and nothing is cleaned up
row by row:

    create  ->  run the suite  ->  drop

* PostgreSQL: a database of its own (default ``sceneops_test``) on the local
  server, migrated with the repository's alembic migrations.
* ArtifactStore: a bucket of its own (default ``sceneops-test``). Every suite
  reads its bucket from ``MINIO_BUCKET``, so isolation needs no special case in
  the tests: the runner points that variable at the disposable bucket.

``create`` first drops whatever a previous run left behind, so an interrupted or
killed run is recovered by running again. ``drop`` is the only destructive
operation and it is reachable only for names that are provably disposable:
a database matching ``sceneops_test*`` and a bucket matching ``sceneops-test*``
that differ from the reference environment's own. The same check refuses, at
pytest session start, a suite whose environment points anywhere else
(``require_disposable_environment``).

CLI (run from the repository root):

    python tests/infrastructure/disposable_env.py run -- <command...>
    python tests/infrastructure/disposable_env.py create | drop | status
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DATABASE = "sceneops_test"
DEFAULT_BUCKET = "sceneops-test"

# The only names a destructive operation is allowed to act on.
_DISPOSABLE_DATABASE = re.compile(r"^sceneops_test(_[a-z0-9_]+)?$")
_DISPOSABLE_BUCKET = re.compile(r"^sceneops-test(-[a-z0-9-]+)?$")

# The reference environment's own names; never disposable, whatever they match.
REFERENCE_DATABASE = "sceneops"
REFERENCE_BUCKET = "sceneops"


class NotDisposableError(RuntimeError):
    """A name that is not provably disposable was given to a destructive step."""


def check_disposable_database(name: str, *, reference: str = REFERENCE_DATABASE) -> str:
    if name == reference or name == REFERENCE_DATABASE:
        raise NotDisposableError(
            f"refusing to use database {name!r}: it is the reference environment's"
        )
    if not _DISPOSABLE_DATABASE.match(name):
        raise NotDisposableError(
            f"refusing to use database {name!r}: a disposable database is named "
            f"{_DISPOSABLE_DATABASE.pattern}"
        )
    return name


def check_disposable_bucket(name: str, *, reference: str = REFERENCE_BUCKET) -> str:
    if name == reference or name == REFERENCE_BUCKET:
        raise NotDisposableError(
            f"refusing to use bucket {name!r}: it is the reference environment's"
        )
    if not _DISPOSABLE_BUCKET.match(name):
        raise NotDisposableError(
            f"refusing to use bucket {name!r}: a disposable bucket is named "
            f"{_DISPOSABLE_BUCKET.pattern}"
        )
    return name


def database_name_of(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def bucket_name_of(root_uri: str) -> str:
    return urlsplit(root_uri).netloc


def check_environment(environ: dict[str, str]) -> None:
    """Raise unless the database and bucket an environment points at are disposable.

    An unset variable is not checked: the suites skip without it and the
    real-infrastructure commands fail on a skip.
    """
    url = environ.get("SCENEOPS_DATABASE_URL")
    if url:
        check_disposable_database(
            database_name_of(url), reference=environ.get("POSTGRES_DB", "")
        )
    bucket = environ.get("MINIO_BUCKET")
    if bucket:
        check_disposable_bucket(bucket)
    # The ArtifactStore root a compose-run publisher would write into.
    root = environ.get("SCENEOPS_WORKER_ARTIFACT__ROOT_URI")
    if root:
        check_disposable_bucket(bucket_name_of(root))


@dataclass(frozen=True)
class Server:
    """The local PostgreSQL server and MinIO endpoint, from the same variables the
    Makefile passes to every host-side test run."""

    host: str
    port: str
    user: str
    password: str
    minio_endpoint: str
    minio_user: str
    minio_password: str

    @classmethod
    def from_environ(cls, environ: dict[str, str]) -> Server:
        return cls(
            host=environ.get("POSTGRES_HOST", "localhost"),
            port=environ.get("POSTGRES_PORT", "5432"),
            user=environ.get("POSTGRES_USER", "sceneops"),
            password=environ.get("POSTGRES_PASSWORD", "sceneops"),
            minio_endpoint=environ.get(
                "MINIO_ENDPOINT_URL",
                f"http://localhost:{environ.get('MINIO_API_PORT', '9000')}",
            ),
            minio_user=environ.get("MINIO_ROOT_USER", "minioadmin"),
            minio_password=environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        )

    def _dsn(self, database: str, driver: str) -> str:
        return (
            f"{driver}://{quote(self.user)}:{quote(self.password)}"
            f"@{self.host}:{self.port}/{database}"
        )

    def database_url(self, database: str) -> str:
        """The SQLAlchemy URL tests and migrations use."""
        return self._dsn(database, "postgresql+asyncpg")

    def admin_dsn(self) -> str:
        # The `postgres` maintenance database: CREATE / DROP DATABASE cannot run
        # inside the database they act on.
        return self._dsn("postgres", "postgresql")


@dataclass(frozen=True)
class DisposableEnvironment:
    database: str
    bucket: str
    server: Server

    @classmethod
    def checked(
        cls,
        *,
        database: str,
        bucket: str,
        server: Server,
        reference_database: str = REFERENCE_DATABASE,
        reference_bucket: str = REFERENCE_BUCKET,
    ) -> DisposableEnvironment:
        check_disposable_database(database, reference=reference_database)
        check_disposable_bucket(bucket, reference=reference_bucket)
        return cls(database=database, bucket=bucket, server=server)

    @property
    def database_url(self) -> str:
        return self.server.database_url(self.database)

    def child_environment(self, base: dict[str, str]) -> dict[str, str]:
        """What a suite sees: the disposable database and bucket, nothing of the
        reference environment's."""
        env = dict(base)
        env["SCENEOPS_DATABASE_URL"] = self.database_url
        env["MINIO_BUCKET"] = self.bucket
        env["MINIO_ENDPOINT_URL"] = self.server.minio_endpoint
        env["MINIO_ROOT_USER"] = self.server.minio_user
        env["MINIO_ROOT_PASSWORD"] = self.server.minio_password
        check_environment(env)
        return env

    # ── PostgreSQL ───────────────────────────────────────────────────────────

    async def _drop_database(self, connection) -> None:
        # FORCE ends sessions a killed run left connected.
        await connection.execute(
            f'DROP DATABASE IF EXISTS "{self.database}" WITH (FORCE)'
        )

    async def _create_database(self) -> None:
        import asyncpg

        connection = await asyncpg.connect(self.server.admin_dsn())
        try:
            await self._drop_database(connection)
            await connection.execute(f'CREATE DATABASE "{self.database}"')
        finally:
            await connection.close()

    async def _database_exists(self) -> bool:
        import asyncpg

        connection = await asyncpg.connect(self.server.admin_dsn())
        try:
            return bool(
                await connection.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", self.database
                )
            )
        finally:
            await connection.close()

    async def _drop_database_standalone(self) -> None:
        import asyncpg

        connection = await asyncpg.connect(self.server.admin_dsn())
        try:
            await self._drop_database(connection)
        finally:
            await connection.close()

    def _migrate(self) -> None:
        env = {**os.environ, "SCENEOPS_DATABASE_URL": self.database_url}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini"]
            + ["upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"migrating {self.database} failed:\n{result.stderr[-4000:]}"
            )

    # ── MinIO ────────────────────────────────────────────────────────────────

    def _s3(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.server.minio_endpoint,
            aws_access_key_id=self.server.minio_user,
            aws_secret_access_key=self.server.minio_password,
            region_name="us-east-1",
        )

    def _bucket_exists(self, s3) -> bool:
        return self.bucket in {b["Name"] for b in s3.list_buckets()["Buckets"]}

    def _remove_bucket(self, s3) -> int:
        """Empty and remove the bucket; returns the number of objects removed."""
        if not self._bucket_exists(s3):
            return 0
        removed = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            # One request per object, like S3ArtifactStore.delete_prefix: the
            # local MinIO rejects a bulk DeleteObjects that carries no Content-MD5.
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=self.bucket, Key=obj["Key"])
                removed += 1
        s3.delete_bucket(Bucket=self.bucket)
        return removed

    def _object_count(self, s3) -> int:
        count = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            count += len(page.get("Contents", []))
        return count

    # ── lifecycle ────────────────────────────────────────────────────────────

    def create(self) -> None:
        """A fresh, migrated database and an empty bucket; leftovers are dropped first."""
        asyncio.run(self._create_database())
        self._migrate()
        s3 = self._s3()
        self._remove_bucket(s3)
        s3.create_bucket(Bucket=self.bucket)

    def drop(self) -> None:
        """Remove the database and the bucket (with its objects); absent is fine."""
        failures: list[Exception] = []
        for step in (
            lambda: asyncio.run(self._drop_database_standalone()),
            lambda: self._remove_bucket(self._s3()),
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - the other resource is still dropped
                failures.append(exc)
        if failures:
            raise failures[0]

    def status(self) -> dict[str, object]:
        s3 = self._s3()
        bucket_exists = self._bucket_exists(s3)
        return {
            "database": self.database,
            "database_exists": asyncio.run(self._database_exists()),
            "bucket": self.bucket,
            "bucket_exists": bucket_exists,
            "bucket_objects": self._object_count(s3) if bucket_exists else 0,
        }

    def run(
        self,
        command: list[str],
        *,
        runtime: str | None = None,
        env_file: str = ".env.local",
    ) -> int:
        """create -> [start the execution runtime] -> command -> [stop it] -> drop.
        The teardown runs on success, failure and interruption, including a create
        or start that fails halfway; a run that cannot tear down (SIGKILL) is
        recovered by the next create / start.

        ``runtime`` names the pipeline orchestrator of an execution runtime
        (execution_runtime.py) started on the disposable database and bucket for
        suites that generate execution history; the suite sees its API."""
        process: subprocess.Popen | None = None
        execution = None
        if runtime is not None:
            from execution_runtime import ExecutionRuntime

            execution = ExecutionRuntime(self, runtime, env_file=env_file)
        child_base = self.child_environment(dict(os.environ))

        def _forward_sigterm(signum, _frame):
            if process is not None and process.poll() is None:
                process.send_signal(signum)

        def _ignore_sigint(_signum, _frame):
            # Ctrl-C reaches the whole foreground process group, so the suite is
            # interrupted exactly once by the terminal; forwarding it again would
            # abort pytest before its fixtures stop their containers and workers.
            pass

        previous = {
            signal.SIGTERM: signal.signal(signal.SIGTERM, _forward_sigterm),
            signal.SIGINT: signal.signal(signal.SIGINT, _ignore_sigint),
        }
        completed = False
        try:
            self.create()
            child = child_base
            if execution is not None:
                execution.up(child)
                child = execution.child_environment(child)
            process = subprocess.Popen(command, cwd=REPO_ROOT, env=child)
            code = process.wait()
            completed = True
            return code
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            # The runtime first: its processes hold connections to the database.
            steps = [self.drop]
            if execution is not None:
                steps.insert(0, lambda: execution.down(child_base))
            failures: list[Exception] = []
            for step in steps:
                try:
                    step()
                except Exception as exc:  # noqa: BLE001 - the later steps still run
                    failures.append(exc)
            if failures:
                if completed:
                    raise failures[0]
                print(f"disposable_env: teardown also failed: {failures}", file=sys.stderr)


def _parse(argv: list[str]) -> argparse.Namespace:
    # The command after `--` is split off first: argparse cannot take a positional
    # list after options.
    command: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["run", "create", "drop", "status"])
    parser.add_argument(
        "--runtime",
        choices=["celery", "airflow"],
        help="run: also start the disposable execution runtime with this pipeline "
        "orchestrator (execution_runtime.py)",
    )
    parser.add_argument("--env-file", default=os.environ.get("ENV_FILE", ".env.local"))
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument(
        "--reference-database",
        default=os.environ.get("POSTGRES_DB", REFERENCE_DATABASE),
        help="the reference environment's database; never disposable",
    )
    parser.add_argument(
        "--reference-bucket",
        default=os.environ.get("MINIO_BUCKET", REFERENCE_BUCKET),
        help="the reference environment's bucket; never disposable",
    )
    args = parser.parse_args(argv)
    args.command = command
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    try:
        environment = DisposableEnvironment.checked(
            database=args.database,
            bucket=args.bucket,
            server=Server.from_environ(dict(os.environ)),
            reference_database=args.reference_database,
            reference_bucket=args.reference_bucket,
        )
    except NotDisposableError as exc:
        print(f"disposable_env: {exc}", file=sys.stderr)
        return 2

    if args.action == "run":
        if not args.command:
            print("disposable_env: run needs a command after `--`", file=sys.stderr)
            return 2
        return environment.run(
            args.command, runtime=args.runtime, env_file=args.env_file
        )
    if args.action == "create":
        environment.create()
    elif args.action == "drop":
        environment.drop()
    else:
        print(environment.status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
