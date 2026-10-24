import json
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from src.config import Config
from src.oauth_web import build_oauth_blueprint
from src.pipeline import run_once
from src.token_store import TokenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

_lock = threading.Lock()
# In-memory state (fast path, correct as long as this one process handles both
# the /run call and the later /status call). Mirrored to disk below so status
# survives a process restart too, as long as DATA_DIR is a persistent volume.
# `uploaded` is deliberately separate from `last_result`: a render-only run and
# a published run produce the same file path, so the path alone never proved an
# upload happened. That ambiguity hid a skipped upload for over a week.
_state = {
    "running": False,
    "last_result": None,
    "last_error": None,
    "started_at": None,
    "uploaded": False,
    "video_url": None,
    "upload_skipped_reason": None,
    "footage_sources": [],
}

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "run_state.json"

# The refresh token lives here rather than in an environment variable, so
# that the re-auth flow below can replace it without a redeploy and the
# refresh path can keep any replacement Google hands back. On Railway this
# directory must be a mounted volume (see railway.toml) or both the token and
# the run state above are lost on every restart.
TOKEN_STORE = TokenStore(DATA_DIR / "tokens.db")

# A run that has been "in progress" longer than this is treated as dead. The
# job runs in a daemon thread inside this process, so if it is killed (Railway
# OOM, container restart, a deploy landing mid-run) the `finally` in _run_job
# never executes and `running: true` is left on disk forever -- which made
# every later /run return 409 busy. Nothing recovered from that on its own.
STALE_AFTER_SECONDS = float(os.environ.get("RUN_STALE_AFTER_SECONDS", "1800"))


def _save_state() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(_state))
    except Exception:
        log.exception("Failed to persist state to %s", STATE_FILE)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.exception("Failed to read state from %s", STATE_FILE)
    return dict(_state)


def _is_stale(state: dict) -> bool:
    """True if `state` claims a run is in progress but it cannot still be alive."""
    if not state.get("running"):
        return False
    started_at = state.get("started_at")
    if not started_at:
        # Running with no start time: unrecoverable by timeout, so treat as dead.
        return True
    return (time.time() - float(started_at)) > STALE_AFTER_SECONDS


def _reclaim_stale_lock() -> None:
    """Clear a `running` flag left behind by a process that died mid-run.

    Called at startup and before each /run. At startup any `running: true` on
    disk is by definition stale -- the only thing that could have set it was a
    thread in a previous process. This assumes a single instance, which the
    in-memory state above already assumes.
    """
    disk_state = _load_state()
    if not disk_state.get("running"):
        return

    age = None
    if disk_state.get("started_at"):
        age = time.time() - float(disk_state["started_at"])
    log.warning(
        "Found an abandoned run on disk (started_at=%s, age=%s). The process that "
        "started it is gone, so clearing the lock.",
        disk_state.get("started_at"),
        f"{age:.0f}s" if age is not None else "unknown",
    )
    _state.update(
        running=False,
        last_result=disk_state.get("last_result"),
        last_error="previous run did not finish (process died mid-run); lock cleared",
        started_at=disk_state.get("started_at"),
    )
    _save_state()


def _run_job() -> None:
    try:
        cfg = Config.load()
        result = run_once(cfg, Path("output"), upload=cfg.auto_upload, store=TOKEN_STORE)
        _state["last_result"] = str(result.video_path)
        _state["last_error"] = None
        _state["uploaded"] = result.uploaded
        _state["video_url"] = result.video_url
        _state["upload_skipped_reason"] = result.upload_skipped_reason
        _state["footage_sources"] = result.footage_sources
        log.info("Footage sources: %s", ", ".join(result.footage_sources) or "none")
        if result.uploaded:
            log.info("Scheduled job finished and published: %s", result.video_url)
        else:
            log.warning(
                "Scheduled job rendered but did NOT publish (%s): %s",
                result.upload_skipped_reason,
                result.video_path,
            )
    except Exception as e:
        _state["last_error"] = str(e)
        _state["uploaded"] = False
        _state["video_url"] = None
        log.exception("Scheduled job failed")
    finally:
        _state["running"] = False
        _save_state()


@app.get("/health")
def health():
    # Read disk, like /status, so the two endpoints cannot disagree about
    # whether a run is in flight.
    disk_state = _load_state()
    return jsonify(status="ok", running=bool(disk_state.get("running"))), 200


@app.get("/status")
def status():
    # Prefer on-disk state: if this request landed on a different process (or
    # the process restarted) than the one that ran the job, the in-memory
    # dict here would still show stale defaults even though a real result
    # exists on disk (or vice versa). Disk is the source of truth when it's
    # newer than what's in memory.
    disk_state = _load_state()
    return jsonify(**disk_state, stale=_is_stale(disk_state)), 200


@app.post("/run")
def run():
    if not _lock.acquire(blocking=False):
        return jsonify(status="busy", message="a run is already in progress"), 409

    try:
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        disk_state = _load_state()

        if force or _is_stale(disk_state):
            _reclaim_stale_lock()
            disk_state = _load_state()

        if disk_state.get("running") or _state["running"]:
            started_at = disk_state.get("started_at") or _state.get("started_at")
            age = f"{time.time() - float(started_at):.0f}s" if started_at else "unknown"
            log.warning(
                "Rejecting /run: a run is already in progress (age=%s). It will be "
                "reclaimed automatically once it passes %.0fs, or call /run?force=true.",
                age,
                STALE_AFTER_SECONDS,
            )
            return (
                jsonify(
                    status="busy",
                    message="a run is already in progress",
                    started_at=started_at,
                    age_seconds=(time.time() - float(started_at)) if started_at else None,
                    hint="retry with ?force=true if the previous run died",
                ),
                409,
            )
        _state.update(
            running=True,
            last_result=None,
            last_error=None,
            started_at=time.time(),
            uploaded=False,
            video_url=None,
            upload_skipped_reason=None,
            footage_sources=[],
        )
        _save_state()
    finally:
        _lock.release()

    thread = threading.Thread(target=_run_job, daemon=True)
    thread.start()
    return jsonify(status="started"), 202


@app.post("/dry-run")
def dry_run():
    """Synchronous, upload-free pipeline run. No background thread, no shared
    state to go stale -- the full result (success or failure) comes back in
    this same HTTP response. Use this to verify the pipeline works at all,
    independent of whatever is going on with /run + /status."""
    try:
        cfg = Config.load()
        result = run_once(cfg, Path("output"), upload=False, store=TOKEN_STORE)
        return jsonify(status="ok", result=str(result.video_path), uploaded=False), 200
    except Exception as e:
        log.exception("Dry run failed")
        return jsonify(status="error", error=str(e)), 500


# /auth/start + /oauth/callback: re-authorizing with Google in a browser
# rewrites the stored token in place, which is the whole point -- the weekly
# expiry used to be repaired by hand through the Railway dashboard.
app.register_blueprint(build_oauth_blueprint(TOKEN_STORE))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))

    # A `running: true` on disk at startup can only have come from a process
    # that no longer exists, so clear it before accepting any traffic.
    _reclaim_stale_lock()

    from waitress import serve

    serve(app, host="0.0.0.0", port=port, threads=4)
