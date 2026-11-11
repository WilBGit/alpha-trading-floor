from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

ALPHA_TRADER_PHRASES = [
    "But here’s the thing…",
    "The interesting part?",
    "I’m watching…",
    "Not seeing full panic yet.",
    "Buyers have a shot.",
    "Bears need confirmation.",
    "Price is telling the story.",
    "Trade the chart. Not the noise.",
    "That’s the level I’m watching.",
    "Momentum is there. Confirmation isn’t.",
    "Don’t chase the first candle.",
]


class TTSEngine(str, Enum):
    MACOS_SAY = "macos_say"
    XTTS_V2 = "xtts_v2"
    OPENAI_TTS = "openai_tts"  # ChatGPT-like natural voice


@dataclass
class TTSConfig:
    engine: TTSEngine = TTSEngine.MACOS_SAY
    voice: str = "Evan (Enhanced)"
    speed: float = 185.0  # WPM
    xtts_reference_wav: str | None = None  # Project Contributor's voice clone path
    openai_model: str = "gpt-4o-mini-tts"
    chunk_size: int = 400  # chars for natural chunking
    pronunciation_dict: dict[str, str] | None = None


def load_tts_config() -> TTSConfig:
    """Load from env or config. Defaults to high-naturalness OpenAI first."""
    engine = os.getenv("TTS_ENGINE", "openai_tts")
    if engine not in [e.value for e in TTSEngine]:
        engine = "openai_tts"

    return TTSConfig(
        engine=TTSEngine(engine),
        voice=os.getenv("TTS_VOICE", "Evan (Enhanced)"),
        speed=float(os.getenv("TTS_SPEED", "185")),
        xtts_reference_wav=os.getenv("XTTS_REFERENCE_WAV"),
        openai_model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        chunk_size=int(os.getenv("TTS_CHUNK_SIZE", "400")),
        pronunciation_dict=None,  # load from config/pronunciation.json in real impl
    )


def normalize_text(text: str, pron_dict: dict[str, str] | None = None) -> str:
    if pron_dict:
        for k, v in pron_dict.items():
            text = text.replace(k, v)
    text = re.sub(r"[#@$]", "", text)
    text = re.sub(r"[𐀀-􏿿]", "", text)
    text = re.sub(r"[\W_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_emojis(text: str) -> str:
    return re.sub(r"[𐀀-􏿿]", "", text)


def chunk_text(text: str, max_chars: int = 400) -> list[str]:
    """Sentence-safe chunking for stable, natural TTS."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current += (" " if current else "") + sent
        else:
            if current:
                chunks.append(current)
            current = sent
    if current:
        chunks.append(current)
    return chunks


class TTSService:
    """Unified TTS with fallback: OpenAI (most natural) → XTTS V2 → macOS say."""

    def __init__(self, config: TTSConfig | None = None):
        self.config = config or load_tts_config()
        self._client = httpx.Client(timeout=60.0) if self.config.engine == TTSEngine.OPENAI_TTS else None

    def synthesize(self, text: str, output_path: str | Path) -> Path:
        text = normalize_text(text, self.config.pronunciation_dict)
        chunks = chunk_text(text, self.config.chunk_size)

        if self.config.engine == TTSEngine.OPENAI_TTS:
            return self._openai_tts(chunks, output_path)
        elif self.config.engine == TTSEngine.XTTS_V2:
            return self._xtts_v2(chunks, output_path)
        else:
            return self._macos_say(chunks, output_path)

    def _openai_tts(self, chunks: list[str], output_path: str | Path) -> Path:
        """ChatGPT-style natural voice via OpenAI TTS (or Codex Responses)."""
        # Placeholder: real impl uses Codex Responses API or openai.Audio.speech
        # For now, fall back to macOS if no key, but prefer this for naturalness
        try:
            # Example structure (implement with actual token from hermes auth)
            # response = self._client.post(..., json={"model": self.config.openai_model, "input": " ".join(chunks), "voice": "alloy"})
            # For scaffold: delegate to macOS until full auth wired
            return self._macos_say(chunks, output_path)
        except Exception:
            return self._macos_say(chunks, output_path)

    def _xtts_v2(self, chunks: list[str], output_path: str | Path) -> Path:
        """Coqui XTTS V2 with voice clone reference for highest naturalness."""
        if not self.config.xtts_reference_wav or not Path(self.config.xtts_reference_wav).exists():
            return self._macos_say(chunks, output_path)

        # Real impl: call coqui-tts venv with reference wav + chunks
        # For scaffold, fall back
        return self._macos_say(chunks, output_path)

    def _macos_say(self, chunks: list[str], output_path: str | Path) -> Path:
        """Deterministic fallback: Evan (Enhanced) at 185 WPM."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            aiff_files = []
            for i, chunk in enumerate(chunks):
                aiff = Path(tmp) / f"chunk_{i}.aiff"
                cmd = [
                    "say",
                    "-v",
                    self.config.voice,
                    "-r",
                    str(self.config.speed),
                    "-o",
                    str(aiff),
                    chunk,
                ]
                subprocess.run(cmd, check=True, capture_output=True)
                aiff_files.append(str(aiff))

            # Concat + convert to mp3 with limiter
            list_file = Path(tmp) / "list.txt"
            list_file.write_text("\n".join(f"file '{f}'" for f in aiff_files))

            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-af",
                "acompressor,alimiter=limit=-3dB",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(output_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        return output_path


# Usage in pipeline:
# tts = TTSService()
# tts.synthesize(script_text, "audio/narration_YYYY-MM-DD.mp3")
