"""Small parameterized SQLite cache/run store; checkpoints live in a separate file."""

import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .ai_models import AIConfigurationError

SCHEMA_VERSION = 1


def default_state_dir() -> Path:
    override = os.environ.get("REPOTRAIL_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "RepoTrail"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "RepoTrail"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "repotrail"


class AttemptBudgetExhausted(Exception):
    pass


class StateStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory if directory is not None else default_state_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "cache.sqlite3"
        self.checkpoint_path = self.directory / "checkpoints.sqlite3"
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise AIConfigurationError(
                    "Unsupported RepoTrail state schema; preserve the existing state directory."
                )
            if version == 0:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone():
                    raise AIConfigurationError(
                        "Unrecognized existing state database; choose a new state directory."
                    )
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        cache_key TEXT PRIMARY KEY, repository_id TEXT NOT NULL,
                        answer_json TEXT NOT NULL, created_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, evidence_fingerprint TEXT NOT NULL,
                        config_fingerprint TEXT NOT NULL, request_json TEXT NOT NULL, package_json TEXT NOT NULL,
                        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        result_json TEXT, created_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS run_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                        node TEXT NOT NULL, elapsed_ms REAL NOT NULL, details_json TEXT NOT NULL,
                        FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE);
                    PRAGMA user_version=1;
                    COMMIT;
                """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_run(
        self,
        run_id: str,
        repository_id: str,
        evidence_fingerprint: str,
        config_fingerprint: str,
        request: dict,
        package: dict,
    ) -> None:
        with self.connection() as connection:
            if connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 256:
                raise AIConfigurationError(
                    "Run retention limit reached; use cache-prune before creating another run."
                )
            try:
                connection.execute(
                    "INSERT INTO runs(run_id, repository_id, evidence_fingerprint, config_fingerprint, request_json, package_json, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        repository_id,
                        evidence_fingerprint,
                        config_fingerprint,
                        json.dumps(request, ensure_ascii=True),
                        json.dumps(package, ensure_ascii=True),
                        "running",
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AIConfigurationError(
                    "Run ID already exists; use resume instead of resetting its attempt budget."
                ) from exc

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row is not None else None

    def charge_attempt(self, run_id: str, maximum: int) -> int:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE runs SET attempts=attempts+1 WHERE run_id=? AND attempts<?",
                (run_id, maximum),
            )
            if cursor.rowcount != 1:
                raise AttemptBudgetExhausted("The persisted provider-attempt budget is exhausted.")
            return connection.execute(
                "SELECT attempts FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]

    def set_evidence(self, run_id: str, fingerprint: str, package: dict) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT evidence_fingerprint FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row[0] not in ("", fingerprint):
                raise AIConfigurationError(
                    "Run evidence changed; start a new run rather than mixing checkpoints."
                )
            connection.execute(
                "UPDATE runs SET evidence_fingerprint=?, package_json=? WHERE run_id=?",
                (fingerprint, json.dumps(package, ensure_ascii=True), run_id),
            )

    def finish_run(self, run_id: str, status: str, result: dict | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE runs SET status=?, result_json=? WHERE run_id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=True) if result is not None else None,
                    run_id,
                ),
            )

    def get_cache(self, key: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT CASE WHEN length(answer_json)<=65536 THEN answer_json ELSE '' END FROM cache_entries WHERE cache_key=?",
                (key,),
            ).fetchone()
            return row[0] if row else None

    def put_cache(self, key: str, repository_id: str, answer: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cache_entries VALUES (?,?,?,?)",
                (key, repository_id, json.dumps(answer, ensure_ascii=True), time.time()),
            )
            connection.execute(
                "DELETE FROM cache_entries WHERE cache_key NOT IN (SELECT cache_key FROM cache_entries ORDER BY created_at DESC, cache_key LIMIT 128)"
            )

    def delete_cache(self, key: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM cache_entries WHERE cache_key=?", (key,))

    def event(self, run_id: str, node: str, elapsed_ms: float, details: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO run_events(run_id,node,elapsed_ms,details_json) VALUES (?,?,?,?)",
                (run_id, node, elapsed_ms, json.dumps(details, ensure_ascii=True)),
            )

    def events(self, run_id: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT node,elapsed_ms,details_json FROM run_events WHERE run_id=? ORDER BY event_id",
                (run_id,),
            ).fetchall()
        return [
            {
                "node": row["node"],
                "elapsed_ms": row["elapsed_ms"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def prune(self, keep: int = 100) -> list[str]:
        if not 0 <= keep <= 256:
            raise AIConfigurationError("Prune retention must be between zero and 256.")
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC, run_id LIMIT -1 OFFSET ?",
                (keep,),
            ).fetchall()
            ids = [row[0] for row in rows]
            connection.executemany("DELETE FROM runs WHERE run_id=?", [(run_id,) for run_id in ids])
            connection.execute(
                "DELETE FROM cache_entries WHERE cache_key NOT IN (SELECT cache_key FROM cache_entries ORDER BY created_at DESC, cache_key LIMIT ?)",
                (keep,),
            )
        return ids
