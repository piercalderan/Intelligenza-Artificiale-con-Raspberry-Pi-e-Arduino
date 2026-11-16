import os, re, urllib.parse, requests
from io import BytesIO
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, render_template
from openai import OpenAI

load_dotenv()
raw_key = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_KEY = re.sub(r"[\r\n]+$", "", raw_key.strip())
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__)  # usa /templates per default

# CORS minimale (utile solo se l'HTML è servito da host diverso)
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

# ---- HOME via template ----
@app.get("/")
def home():
    # Se vuoi passare qualcosa al template, aggiungi parametri qui
    return render_template("index.html")

# --- (sotto lascia invariati i tuoi endpoint /api/ask, /api/tts, /api/image_wiki) ---
# ... (tieni la stessa implementazione che hai ora)


# ---- API: Chat (Responses API simplificata) ----
@app.post("/api/ask")
def ask():
    data = request.get_json(force=True)
    q = (data.get("q") or "").strip()
    if not q:
        return jsonify(error="Domanda vuota"), 400
    r = client.responses.create(model="gpt-4o-mini", input=q)
    return jsonify(text=r.output_text)

# ---- API: TTS (GET e POST) -> MP3 ----
@app.route("/api/tts", methods=["GET","POST"])
def tts():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        voice = (data.get("voice") or "alloy").strip()
    else:
        text  = (request.args.get("text") or "").strip()
        voice = (request.args.get("voice") or "alloy").strip()
    if not text:
        return "Missing text", 400

    speech = client.audio.speech.create(model="gpt-4o-mini-tts", voice=voice, input=text)
    return send_file(BytesIO(speech.read()), mimetype="audio/mpeg",
                     as_attachment=False, download_name="reply.mp3")

# ---- API: Immagine da Wikipedia ----
def _fix_url(u: str) -> str:
    return "https:" + u if u.startswith("//") else u

@app.get("/api/image_wiki")
def image_wiki():
    prompt = (request.args.get("prompt") or "").strip()
    if not prompt:
        return "Missing ?prompt=...", 400

    UA = {"User-Agent": "Pi-AI-Gateway/1.0 (contact: you@example.com)"}
    langs = ["it", "en", "el"]

    for lang in langs:
        try:
            p = {
                "action": "query", "generator": "search", "gsrsearch": prompt,
                "gsrlimit": 1, "prop": "pageimages", "piprop": "original|thumbnail",
                "pithumbsize": 1400, "redirects": 1, "format": "json"
            }
            r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                             params=p, headers=UA, timeout=10)
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            if pages:
                page = next(iter(pages.values()))
                title = page.get("title", prompt)
                page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"
                img_url = (page.get("original") or {}).get("source") \
                       or (page.get("thumbnail") or {}).get("source")
                if img_url:
                    img = requests.get(_fix_url(img_url), headers=UA, timeout=15)
                    img.raise_for_status()
                    resp = send_file(BytesIO(img.content),
                                     mimetype=img.headers.get("Content-Type","image/jpeg"),
                                     as_attachment=False, download_name="image")
                    resp.headers["X-Wiki-Lang"]  = lang
                    resp.headers["X-Wiki-Title"] = title
                    resp.headers["X-Wiki-Page"]  = page_url
                    resp.headers["Cache-Control"] = "no-store"
                    return resp

            # Fallback via REST summary
            s = requests.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(prompt)}",
                             headers=UA, timeout=10)
            if s.status_code == 200:
                data = s.json()
                title = data.get("title", prompt)
                page_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"
                img_url = ((data.get("originalimage") or {}).get("source")
                           or (data.get("thumbnail") or {}).get("source"))
                if img_url:
                    img = requests.get(_fix_url(img_url), headers=UA, timeout=15)
                    if img.ok:
                        resp = send_file(BytesIO(img.content),
                                         mimetype=img.headers.get("Content-Type","image/jpeg"),
                                         as_attachment=False, download_name="image")
                        resp.headers["X-Wiki-Lang"]  = lang
                        resp.headers["X-Wiki-Title"] = title
                        resp.headers["X-Wiki-Page"]  = page_url
                        resp.headers["Cache-Control"] = "no-store"
                        return resp
        except Exception:
            continue

    return jsonify(error="no_image_found"), 404

if __name__ == "__main__":
    # Apri la pagina da altri dispositivi in LAN: http://<IP_DEL_PI>:5000/
    app.run(host="0.0.0.0", port=5000)
