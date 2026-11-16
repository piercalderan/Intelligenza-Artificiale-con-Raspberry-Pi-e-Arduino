import os, json, base64, tempfile
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import openai

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")
BROKER = os.getenv("MQTT_BROKER")
PORT   = int(os.getenv("MQTT_PORT"))

client = mqtt.Client(client_id="ai_worker")

def on_connect(c, userdata, flags, rc):
    print("MQTT connected", rc)
    c.subscribe("ai/requests/chat")
    c.subscribe("ai/requests/tts")

def on_message(c, userdata, msg):
    topic = msg.topic
    print(topic)
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        print(data)
    except Exception:
        print("invalid json")
        return

    req_id = str(data.get("id", "noid"))
    print(data.get("message", ""))

    if topic == "ai/requests/chat":
        user_msg = data.get("message", "")
        
        if not user_msg:
            return
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":user_msg}],
            temperature=0.3
        )
        text = resp.choices[0].message.content
        reply_topic = f"ai/replies/chat/{req_id}"
        c.publish(reply_topic, json.dumps({"id": req_id, "reply": text}), qos=1)

    elif topic == "ai/requests/tts":
        text = data.get("text", "")
        voice = data.get("voice", "alloy")
        if not text:
            return
        audio = openai.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text
        )
        # Opzione A: invio come base64 (comodo in MQTT)
        b = audio.read()
        b64 = base64.b64encode(b).decode("ascii")
        reply_topic = f"ai/replies/tts/{req_id}"
        c.publish(reply_topic, json.dumps({"id": req_id, "audio_b64": b64, "format": "mp3"}), qos=1)

client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, 60)
client.loop_forever()
