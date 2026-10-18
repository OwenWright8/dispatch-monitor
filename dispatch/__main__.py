"""Entry point.

    python -m dispatch                 poll + transcribe + serve dashboard (default)
    python -m dispatch reprocess       rebuild incidents from stored transcripts
    python -m dispatch reprocess --rules   ...without the LLM
"""

import argparse
import logging
import threading

from . import config, db, pipeline


def main():
    ap = argparse.ArgumentParser(prog="dispatch")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "reprocess"])
    ap.add_argument("--rules", action="store_true", help="reprocess with keyword rules only")
    ap.add_argument("--no-web", action="store_true", help="run the pipeline without the HTTP server")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "faster_whisper", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("dispatch")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init()

    if args.command == "reprocess":
        pipeline.reprocess(use_llm=not args.rules)
        return

    log.info(f"Monitoring {len(config.CHANNELS)} channels: "
             + ", ".join(f"{c.agency} ({c.talkgroup})" for c in config.CHANNELS.values()))
    log.info(f"Whisper {config.WHISPER_MODEL} | LLM "
             + (f"{config.OLLAMA_MODEL} @ {config.OLLAMA_URL}" if config.LLM_ENABLED else "disabled (rules only)"))
    stop = threading.Event()
    threading.Thread(target=pipeline.poller_loop, args=(stop,), name="poller", daemon=True).start()
    threading.Thread(target=pipeline.worker_loop, args=(stop,), name="worker", daemon=True).start()

    if args.no_web:
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
        return
    import uvicorn
    from .api import app
    log.info(f"Dashboard on http://{config.WEB_HOST}:{config.WEB_PORT}/")
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
    stop.set()


if __name__ == "__main__":
    main()
