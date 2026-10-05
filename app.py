import json
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from src.config import Config
from src.pipeline import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

_lock = threading.Lock()
# In-memory state (fast path, correct as long as this one process handles both
# the /run call and the later /status call). Mirrored to disk below so status
# survives a process restart too, as long as DATA_DIR is a persistent volume.
_state = {"running": False, "last_result": None, "last_error": None, "started_at": None}

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "run_state.json"


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


def _run_job() -> None:
    try:
        cfg = Config.load()
        out_path = run_once(cfg, Path("output"), upload=cfg.auto_upload)
        _state["last_result"] = str(out_path)
        _state["last_error"] = None
        log.info("Scheduled job finished: %s", out_path)
    except Exception as e:
        _state["last_error"] = str(e)
        log.exception("Scheduled job failed")
    finally:
        _state["running"] = False
        _save_state()


@app.get("/health")
def health():
    return jsonify(status="ok", running=_state["running"]), 200


@app.get("/status")
def status():
    # Prefer on-disk state: if this request landed on a different process (or
    # the process restarted) than the one that ran the job, the in-memory
    # dict here would still show stale defaults even though a real result
    # exists on disk (or vice versa). Disk is the source of truth when it's
    # newer than what's in memory.
    disk_state = _load_state()
    return jsonify(**disk_state), 200


def _check_auth() -> tuple[bool, str]:
    expected_token = os.environ.get("RUN_TOKEN")
    if not expected_token:
        return False, "RUN_TOKEN is not configured on the server"
    auth_header = request.headers.get("Authorization", "")
    provided = auth_header.removeprefix("Bearer ").strip()
    if provided != expected_token:
        return False, "unauthorized"
    return True, ""


@app.post("/run")
def run():
    ok, err = _check_auth()
    if not ok:
        return jsonify(error=err), (401 if err == "unauthorized" else 500)

    if not _lock.acquire(blocking=False):
        return jsonify(status="busy", message="a run is already in progress"), 409

    try:
        disk_state = _load_state()
        if disk_state.get("running") or _state["running"]:
            return jsonify(status="busy", message="a run is already in progress"), 409
        _state.update(running=True, last_result=None, last_error=None, started_at=time.time())
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
    ok, err = _check_auth()
    if not ok:
        return jsonify(error=err), (401 if err == "unauthorized" else 500)

    try:
        cfg = Config.load()
        out_path = run_once(cfg, Path("output"), upload=False)
        return jsonify(status="ok", result=str(out_path)), 200
    except Exception as e:
        log.exception("Dry run failed")
        return jsonify(status="error", error=str(e)), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve

    serve(app, host="0.0.0.0", port=port, threads=4)
