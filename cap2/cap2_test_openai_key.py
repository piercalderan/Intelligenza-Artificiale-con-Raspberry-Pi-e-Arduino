#Esempio minimale di chiamata a GPT con chiave caricata da variabile d'ambiente

import os
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")
response = openai.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Ciao, spiegami cos'è Arduino."}])
print(response.choices[0].message.content)


#Esempio minimale di chiamata a GPT con chiave caricata da dotenv e .env
import openai
from dotenv import load_dotenv
import os

def load_openai_key(): #funzione di chiamata a dotenv
    load_dotenv()  # legge .env se presente
    raw = os.getenv("OPENAI_API_KEY", "")
    key = raw.strip()           # rimuove spazi/newline
    assert key.startswith("sk-"), "OPENAI_API_KEY mancante o formattata male"
    os.environ["OPENAI_API_KEY"] = key   # normalizza nel processo
    return key

openai.api_key = load_openai_key() #chiamata alla funzione

response = openai.chat.completions.create(model="gpt-4o-mini",
                                          messages=
                                          [{"role": "user", "content": "Ciao, chi fu einstein?"}])
print(response.choices[0].message.content)
