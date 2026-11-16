from openai import OpenAI
client = OpenAI()

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "Sei un assistente tecnico conciso."},
        {"role": "user", "content": "Ciao, spiegami cos'è Arduino."}
    ],
    temperature=0.7
)

print(response.choices[0].message.content)
