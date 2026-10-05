import logging
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request

from src.config import Config
from src.pipeline import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

_lock = threading.Lock()
_state = {"running": False, "last_result": None, "last_error": None}


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


@app.get("/health")
def health():
    return jsonify(status="ok", running=_state["running"]), 200


@app.get("/status")
def status():
    return jsonify(**_state), 200


@app.post("/run")
def run():
    expected_token = os.environ.get("RUN_TOKEN")
    if not expected_token:
        return jsonify(error="RUN_TOKEN is not configured on the server"), 500

    auth_header = request.headers.get("Authorization", "")
    provided = auth_header.removeprefix("Bearer ").strip()
    if provided != expected_token:
        return jsonify(error="unauthorized"), 401

    if not _lock.acquire(blocking=False):
        return jsonify(status="busy", message="a run is already in progress"), 409

    try:
        if _state["running"]:
            return jsonify(status="busy", message="a run is already in progress"), 409
        _state["running"] = True
    finally:
        _lock.release()

    thread = threading.Thread(target=_run_job, daemon=True)
    thread.start()
    return jsonify(status="started"), 202


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
