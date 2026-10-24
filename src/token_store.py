"""Durable home for the YouTube OAuth refresh token.

The refresh token used to live only in YOUTUBE_REFRESH_TOKEN, which meant code
could never fix its own credentials: every expiry had to be repaired by hand by
running the local authorize script, copying the token out, pasting it into the
Railway dashboard and waiting for a redeploy. Here the token lives in SQLite
instead, so both the refresh path (src/youtube_client.py) and the browser
re-auth flow (src/oauth_web.py) can rewrite it in place while the service runs.

The env var is still read, but only ever to seed an empty store.

On Railway this file must sit on a mounted volume, otherwise a restart drops
the store back to the env-var seed -- see the volume note in railway.toml.
"""

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger("token_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_tokens (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    refresh_token   TEXT    NOT NULL,
    obtained_at     REAL    NOT NULL,
    last_refresh_ok REAL,
    last_error      TEXT
);
"""

# Added after the store shipped, so existing databases need it bolted on.
_MIGRATIONS = {
    # The env var value we last adopted. Without this we cannot tell "the env
    # var is the stale leftover of a token we already rotated past" from "a
    # human just pasted a fresh token in". The first must be ignored, the
    # second must win, and both look identical without a record of what we took.
    "env_seed": "ALTER TABLE youtube_tokens ADD COLUMN env_seed TEXT",
}


@dataclass(frozen=True)
class StoredToken:
    refresh_token: str
    obtained_at: float
    last_refresh_ok: Optional[float] = None
    last_error: Optional[str] = None
    env_seed: Optional[str] = None


class TokenStore:
    """Single-row SQLite table holding the one refresh token this app uses.

    A connection is opened per call rather than kept around: the Flask worker
    threads and the background run thread all touch this, and a short-lived
    connection per operation is the one sqlite3 usage that is safe across
    threads without any locking of our own.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.db_path.exists()
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            # WAL so a /status read can never block on the run thread's write.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            have = {r[1] for r in conn.execute("PRAGMA table_info(youtube_tokens)")}
            for column, ddl in _MIGRATIONS.items():
                if column not in have:
                    conn.execute(ddl)
            if not existed:
                # The file holds a live credential; keep it owner-only.
                try:
                    os.chmod(self.db_path, 0o600)
                except OSError:
                    log.warning("Could not tighten permissions on %s", self.db_path)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def read(self) -> Optional[StoredToken]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT refresh_token, obtained_at, last_refresh_ok, last_error, "
                "env_seed FROM youtube_tokens WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return StoredToken(
            refresh_token=row["refresh_token"],
            obtained_at=row["obtained_at"],
            last_refresh_ok=row["last_refresh_ok"],
            last_error=row["last_error"],
            env_seed=row["env_seed"],
        )

    def save(self, refresh_token: str, *, env_seed: Optional[str] = None) -> None:
        """Replace the stored token. Clears last_error: this one is untested."""
        if not refresh_token:
            raise ValueError("refresh_token must not be empty")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO youtube_tokens (id, refresh_token, obtained_at, "
                "last_refresh_ok, last_error, env_seed) "
                "VALUES (1, ?, ?, NULL, NULL, ?) "
                "ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token, "
                "obtained_at = excluded.obtained_at, last_refresh_ok = NULL, "
                "last_error = NULL, env_seed = COALESCE(excluded.env_seed, env_seed)",
                (refresh_token, time.time(), env_seed),
            )
        log.info("Saved a new refresh token to %s", self.db_path)

    def sync_from_env(self, refresh_token: str) -> bool:
        """Adopt YOUTUBE_REFRESH_TOKEN when a human has changed it. True if taken.

        Three cases, and the distinction between the last two is the whole
        point of `env_seed`:

        - store empty          -> adopt (first boot, or a store that was lost)
        - env == env_seed      -> ignore. We already took this value; anything
                                  in the store now is the same token or a
                                  rotation of it, so the store is newer.
        - env != env_seed      -> adopt. Only a person editing the variable can
                                  cause this, and they did it to fix something.

        Without the third case, pasting a fresh token into the variable would
        silently do nothing whenever the store held a dead one -- which is
        exactly when someone would reach for it.
        """
        if not refresh_token:
            return False
        stored = self.read()
        if stored is not None and stored.env_seed == refresh_token:
            return False
        self.save(refresh_token, env_seed=refresh_token)
        return True

    def mark_refresh_ok(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE youtube_tokens SET last_refresh_ok = ?, last_error = NULL "
                "WHERE id = 1",
                (time.time(),),
            )

    def mark_error(self, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE youtube_tokens SET last_error = ? WHERE id = 1",
                (message[:500],),
            )
