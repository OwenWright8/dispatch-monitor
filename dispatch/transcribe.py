"""Speech-to-text with faster-whisper (CTranslate2).

Much lighter than openai-whisper: no PyTorch, int8 on CPU, ~4x faster. small.en
transcribes a 15 s dispatch clip in about 2 s on an Apple M-series CPU core
set and a few seconds on an Intel N-series / Pentium NAS.
"""

from __future__ import annotations

import logging
import threading

from . import config

log = logging.getLogger(__name__)

# Biases Whisper toward local vocabulary. Keep it under ~200 tokens.
PROMPT = (
    "Tippecanoe County fire and EMS dispatch, Lafayette and West Lafayette, Indiana. "
    "Station 1, Engine 1502, Medic 5, Westside Medic, Central Medic, Battalion 14, Ladder 12. "
    "Medical run, stand by for dispatch, go channel 2, time out 1110. "
    "North Salisbury Street, Sagamore Parkway, Teal Road, Creasy Lane, Greenbush Street, "
    "Kossuth Street, Concord Road, McCarty Lane, Yeager Road, Klondike Road, Northwestern Avenue, "
    "Cumberland Avenue, Stadium Avenue, State Street, South 18th Street, County Road 500 East. "
    "Westminster Village, LTHC, IU Health Arnett, Franciscan, Purdue."
)

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info(f"Loading Whisper model {config.WHISPER_MODEL} ({config.WHISPER_COMPUTE})")
        _model = WhisperModel(
            config.WHISPER_MODEL, device="cpu", compute_type=config.WHISPER_COMPUTE,
            cpu_threads=config.WHISPER_THREADS, download_root=str(config.DATA_DIR / "models"),
        )
    return _model


def transcribe(path: str) -> str:
    with _lock:
        model = _get_model()
        segments, _ = model.transcribe(
            path, language="en", beam_size=config.WHISPER_BEAM, vad_filter=True,
            initial_prompt=PROMPT, condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
