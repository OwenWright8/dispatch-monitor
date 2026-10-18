"""Download everything the app would otherwise fetch on first start, so it can
be baked into the Docker image:

    <out>/whisper/<model>/                 faster-whisper model
    <out>/tiger/index-<fips>-<year>.pkl    street index, one per county profile
    <out>/places-<fips>.json               named places (OSM), one per county profile

Only the Whisper model is required; the others fall back to a runtime
download if a free public service happens to be down during the build.
"""

import logging
import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def run(out: str, county_only: bool = False):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if county_only:
        _county_data(out_dir)
        return

    from faster_whisper.utils import download_model
    wdir = out_dir / "whisper" / config.WHISPER_MODEL
    log.info(f"Downloading Whisper {config.WHISPER_MODEL} -> {wdir}")
    download_model(config.WHISPER_MODEL, output_dir=str(wdir))
    if not (wdir / "model.bin").exists():
        sys.exit(f"Whisper model download failed: {wdir}")
    shutil.rmtree(wdir / ".cache", ignore_errors=True)

    # County data is chosen at import time (config.PROFILE), so each county
    # gets its own process.
    from .counties import available
    for key in available():
        log.info(f"── County data: {key}")
        subprocess.run([sys.executable, "-m", "dispatch", "prefetch", "--out", str(out_dir), "--county-only"],
                       env={**os.environ, "COUNTY": key}, check=False)
    log.info(f"Prefetch complete: {out_dir}")


def _county_data(out_dir: Path):
    from .geo import Gazetteer, TigerIndex, index_name, places_name
    tdir = out_dir / "tiger"
    try:
        data = TigerIndex._build(tdir)
        with open(tdir / index_name(), "wb") as f:
            pickle.dump(data, f)
        for z in tdir.glob("*.zip"):
            z.unlink()
    except Exception as e:
        log.warning(f"{config.PROFILE.name} street index not bundled ({e}); the app will build it on first start")

    g = Gazetteer(out_dir / places_name())
    if not g.places:
        log.warning(f"{config.PROFILE.name} place names not bundled; the app will download them on first start")
