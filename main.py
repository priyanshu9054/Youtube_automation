import argparse
import logging
from pathlib import Path

from src.config import Config
from src.pipeline import run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate (and optionally upload) one YouTube Short.")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the result to YouTube. Overrides AUTO_UPLOAD if passed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and render video locally without uploading to YouTube.",
    )
    parser.add_argument(
        "--out-dir",
        default="output",
        help="Directory to save the rendered video (default: ./output)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cfg = Config.load()
    upload = (args.upload or cfg.auto_upload) and not args.dry_run

    run_once(cfg, Path(args.out_dir), upload)


if __name__ == "__main__":
    main()
