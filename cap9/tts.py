import os
import uuid
from gtts import gTTS

# Nuova SDK OpenAI (>=1.0)
try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except Exception:
    _OPENAI_AVAILABLE = False

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


def tts_gtts(text: str, lang: str = "it") -> str:
    """
    Sintesi vocale con gTTS (offline-friendly come fallback).
    Ritorna il percorso del file MP3 generato.
    """
    fn = os.path.join(AUDIO_DIR, f"tts_{uuid.uuid4().hex}.mp3")
    tts = gTTS(text=text, lang=lang)
    tts.save(fn)
    return fn


def tts_openai(
    text: str,
    voice: str = "alloy",
    fmt: str = "mp3",
    lang_hint: str = "it",
    model: str = "gpt-4o-mini-tts",
) -> str:
    """
    Sintesi vocale con OpenAI TTS (nuova API). Se non disponibile, ricade su gTTS.
    - voice: es. "alloy", "verse", "nova" (dipende dalla disponibilità del tuo account).
    - fmt: "mp3" (consigliato). Altri formati: "wav", "ogg".
    - lang_hint: usato solo come informazione di contesto; la lingua è dedotta dal testo.
    - model: modello TTS (default: gpt-4o-mini-tts).
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not (_OPENAI_AVAILABLE and api_key):
        # Nessuna key o pacchetto non disponibile → fallback
        return tts_gtts(text, lang="it" if lang_hint == "it" else "el")

    client = OpenAI(api_key=api_key)
    fn = os.path.join(AUDIO_DIR, f"tts_{uuid.uuid4().hex}.{fmt}")

    # Prova prima lo streaming (più efficiente); se non disponibile, usa la create “non-streaming”.
    try:
        # Alcune versioni della SDK espongono lo streaming così:
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            format=fmt,
        ) as resp:
            resp.stream_to_file(fn)
            return fn
    except Exception:
        pass

    # Fallback non-streaming
    try:
        audio = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            format=fmt,
        )
        # Diversi rilasci possono restituire oggetti leggermente diversi: gestiamo i casi comuni
        data = getattr(audio, "content", None)
        if data is None:
            try:
                data = audio.read()
            except Exception:
                # estremo fallback: tentiamo bytes()
                try:
                    data = bytes(audio)
                except Exception:
                    data = None
        if not data:
            # Se qualcosa è andato storto, usa gTTS
            return tts_gtts(text, lang="it" if lang_hint == "it" else "el")

        with open(fn, "wb") as f:
            f.write(data)
        return fn
    except Exception:
        # Qualsiasi errore → fallback gTTS
        return tts_gtts(text, lang="it" if lang_hint == "it" else "el")
