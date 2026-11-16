import os
from openai import OpenAI

# nuova sintassi: serve creare il client

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))


##SYSTEM_PROMPT = (
##    "Sei un assistente domotico su Raspberry Pi. "
##    "Rispondi in modo conciso, in italiano. "
##    "Se l'utente chiede dati sensori locali, consiglia di dire 'temperatura' o 'umidità'."
##)

SYSTEM_PROMPT = (
    "Sei un assistente domotico su Raspberry Pi. "
    "Rispondi in modo conciso, in italiano "
    "Solo e soltanto se l'utente chiede dati di 'temperatura' o di 'umidità'."
)

def ask_gpt(user_text: str) -> str:
    
    if not os.getenv("OPENAI_API_KEY"):
        return "Per risposte intelligenti abilita GPT (manca OPENAI_API_KEY nel file .env)."

    try:
        # nuova API unificata
        response = client.chat.completions.create(
            model="gpt-4o-mini", #gpt-4o-mini"
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Ho avuto un problema con GPT: {e}"
