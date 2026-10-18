"""Speech-to-text with faster-whisper (CTranslate2).

Much lighter than openai-whisper: no PyTorch, int8 on CPU, ~4x faster. small.en
transcribes a 15 s dispatch clip in about 2 s on an Apple M-series CPU core
set and a few seconds on an Intel N-series / Pentium NAS.
"""

from __future__ import annotations

import logging
import threading

from . import config, settings

log = logging.getLogger(__name__)

# Biases Whisper toward local vocabulary (department names, streets). It comes
# from the county profile; keep it under ~200 tokens.
PROMPT = config.PROFILE.whisper_prompt
# Department names Whisper should prefer when the audio is ambiguous.
HOTWORDS = " ".join(dict.fromkeys(d.spoken or d.name for d in config.PROFILE.departments))

_model = None
_model_name = None
_lock = threading.Lock()


def _get_model():
    """Load the configured model; reload when it's changed in the dashboard."""
    global _model, _model_name
    name = settings.get("WHISPER_MODEL")
    if _model is None or name != _model_name:
        from faster_whisper import WhisperModel
        bundled = config.BUNDLE_DIR / "whisper" / name
        model = str(bundled) if (bundled / "model.bin").exists() else name
        log.info(f"Loading Whisper model {name} ({config.WHISPER_COMPUTE})"
                 + (" from image" if model != name else " (downloading if needed)"))
        _model = WhisperModel(
            model, device="cpu", compute_type=config.WHISPER_COMPUTE,
            cpu_threads=config.WHISPER_THREADS, download_root=str(config.DATA_DIR / "models"),
        )
        _model_name = name
    return _model


def transcribe(path: str) -> str:
    with _lock:
        model = _get_model()
        segments, _ = model.transcribe(
            path, language="en", beam_size=settings.get("WHISPER_BEAM"), vad_filter=True,
            initial_prompt=PROMPT, hotwords=HOTWORDS, condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
