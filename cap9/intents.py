import re

def norm(text: str) -> str:
    t = text.strip().lower()
    # normalizzazioni leggere per l'ASR
    t = t.replace("  ", " ")
    return t

def detect_intent(text: str):
    """
    Intenti:
      - temperature_query
      - humidity_query
      - led_on
      - led_off
      - help
      - general
    Regole robuste: accetta coniugazioni e sinonimi (luce/led).
    """
    t = norm(text)

    # help
    if any(k in t for k in ["aiuto", "help", "comandi", "cosa posso dire"]):
        return "help", {}

    # sensori
    if re.search(r"\btemperatura\b", t):
        return "temperature_query", {}

    if re.search(r"\bumidit[àa]\b", t):
        return "humidity_query", {}

    # --- LED / LUCE: accendi/spegni coniugazioni ---
    # qualsiasi parola che inizi con "accend" (accendi/accende/accendere/accendimi...) + (led|luce)
    if re.search(r"accend\w*", t) and re.search(r"\b(led|luce)\b", t):
        return "led_on", {}

    # qualsiasi parola che inizi con "spegn" (spegni/spegne/spegnere/spegnimi...) + (led|luce)
    if re.search(r"spegn\w*", t) and re.search(r"\b(led|luce)\b", t):
        return "led_off", {}

    # fallback: pattern extra comuni
    if any(k in t for k in ["accendi led", "accende il led", "accendi la luce", "accende la luce"]):
        return "led_on", {}
    if any(k in t for k in ["spegni led", "spegne il led", "spegni la luce", "spegne la luce"]):
        return "led_off", {}

    return "general", {"text": t}
