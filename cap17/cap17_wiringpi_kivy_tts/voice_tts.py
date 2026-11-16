# voice_tts.py
import os, threading, tempfile, subprocess
from pathlib import Path

# pip install openai
from openai import OpenAI

# Richiede: export OPENAI_API_KEY=sk-...
_client = OpenAI()

# voce suggerita: alloy (multi-lingua), ma vanno bene anche altre
VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")

def _play_mp3(path: Path):
    # riproduzione non bloccante (richiede: sudo apt-get install mpg123)
    try:
        subprocess.Popen(["mpg123", "-q", str(path)])
    except FileNotFoundError:
        # fallback molto semplice con 'aplay' se converti a WAV (non lo facciamo qui)
        pass

def _speak_worker(text: str):
    # genera MP3 temporaneo e riproduce
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp = Path(f.name)
    # API TTS (stream-to-file)
    # docs: platform.openai.com/docs/guides/text-to-speech
    
    with _client.audio.speech.with_streaming_response.create(
        model=MODEL,
        voice=VOICE,
        input=text,
        #format="mp3",
    ) as resp:
        resp.stream_to_file(tmp)
    _play_mp3(tmp)

def say(text: str):
    """Avvia la sintesi in un thread separato."""
    if not text or not os.getenv("OPENAI_API_KEY"):
        return
    threading.Thread(target=_speak_worker, args=(text,), daemon=True).start()
