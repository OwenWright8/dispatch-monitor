"""Download everything the app would otherwise fetch on first start, so it can
be baked into the Docker image:

    <out>/whisper/<model>/        faster-whisper model
    <out>/tiger/index-<year>.pkl  county street index
    <out>/places.json             named places (OSM)

Only the Whisper model is required; the others fall back to a runtime
download if a free public service happens to be down during the build.
"""

import logging
import pickle
import shutil
import sys
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def run(out: str):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from faster_whisper.utils import download_model
    wdir = out_dir / "whisper" / config.WHISPER_MODEL
    log.info(f"Downloading Whisper {config.WHISPER_MODEL} -> {wdir}")
    download_model(config.WHISPER_MODEL, output_dir=str(wdir))
    if not (wdir / "model.bin").exists():
        sys.exit(f"Whisper model download failed: {wdir}")
    shutil.rmtree(wdir / ".cache", ignore_errors=True)

    from .geo import Gazetteer, TigerIndex
    tdir = out_dir / "tiger"
    try:
        data = TigerIndex._build(tdir)
        with open(tdir / f"index-{config.TIGER_YEAR}.pkl", "wb") as f:
            pickle.dump(data, f)
        for z in tdir.glob("*.zip"):
            z.unlink()
    except Exception as e:
        log.warning(f"TIGER street index not bundled ({e}); the app will build it on first start")

    g = Gazetteer(out_dir / "places.json")
    if not g.places:
        log.warning("Place names not bundled; the app will download them on first start")

    log.info(f"Prefetch complete: {out_dir}")
