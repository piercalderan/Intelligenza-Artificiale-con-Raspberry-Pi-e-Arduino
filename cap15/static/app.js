const outText   = document.getElementById('outText');
const outIntent = document.getElementById('outIntent');
const player    = document.getElementById('player');

const btnLEDOn  = document.getElementById('btnLEDOn');
const btnLEDOff = document.getElementById('btnLEDOff');
const btnAskT   = document.getElementById('btnAskT');
const btnAskH   = document.getElementById('btnAskH');
const btnRec    = document.getElementById('btnRec');
const btnASR    = document.getElementById('btnASR');
const askText   = document.getElementById('askText');
const btnAsk    = document.getElementById('btnAsk');

let mediaRecorder = null;
let chunks = [];
let chosenMime = null;

// ---------- Pulsanti seriale ----------
async function sendCommand(cmd) {
  const res = await fetch('/api/command', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({cmd})
  });
  const data = await res.json();
  if (data.error) alert(data.error);
}
btnLEDOn.onclick  = () => sendCommand('LED_ON');
btnLEDOff.onclick = () => sendCommand('LED_OFF');

// ---------- Intents testuali ----------
btnAskT.onclick = () => askTextAPI('temperatura');
btnAskH.onclick = () => askTextAPI('umidità');
btnAsk.onclick  = () => askTextAPI(askText.value || '');

async function askTextAPI(text) {
  const res = await fetch('/api/assistant', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text})
  });
  const data = await res.json();
  renderResult(data, false);
}

// ---------- ASR ----------
btnRec.addEventListener('mousedown', startRec);
btnRec.addEventListener('touchstart', startRec, { passive: true });
window.addEventListener('mouseup', stopRec);
window.addEventListener('touchend', stopRec);
btnASR.addEventListener('click', async () => {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    await waitForStop(mediaRecorder);
  }
  await sendAudio('/api/asr');
});

async function startRec() {
  if (mediaRecorder && mediaRecorder.state === 'recording') return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    const candidates = ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/ogg'];
    chosenMime = candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
    const opts = chosenMime ? { mimeType: chosenMime } : undefined;

    mediaRecorder = new MediaRecorder(stream, opts);
    chunks = [];
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
    mediaRecorder.onstart = () => btnRec.textContent = '🎙️ Registrando...';
    mediaRecorder.onstop  = () => btnRec.textContent = '🎙️ Premi per parlare';
    mediaRecorder.start();
  } catch (e) {
    alert('Permesso microfono negato o HTTPS mancante.');
  }
}
async function stopRec() {
  if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
  mediaRecorder.stop();
}
function waitForStop(rec) {
  return new Promise(resolve => {
    if (!rec || rec.state !== 'recording') return resolve();
    const onStop = () => { rec.removeEventListener('stop', onStop); resolve(); };
    rec.addEventListener('stop', onStop);
  });
}
async function sendAudio(url) {
  if (!chunks.length) { await new Promise(r => setTimeout(r, 50)); }
  if (!chunks.length) return alert('Nessun audio registrato.');
  const blob = new Blob(chunks, { type: chosenMime || 'application/octet-stream' });
  chunks = [];
  const fd = new FormData();
  fd.append('audio', blob, chosenMime && chosenMime.includes('ogg') ? 'audio.ogg':'audio.webm');

  const res = await fetch(url, { method:'POST', body: fd });
  const data = await res.json();
  if (data.error) return alert(data.error);
  renderResult(data, false);
}

function renderResult(data) {
  outText.textContent = (data.reply || data.text || '') + '';
  if (data.intent && data.intent.name) {
    outIntent.textContent = `Intent: ${data.intent.name} (conf: ${data.intent.confidence ?? '—'})`;
  } else outIntent.textContent = 'Intent: —';
  if (data.audio_url) { player.src = data.audio_url; player.play().catch(()=>{}); }
}

// ---------- Grafico DHT ----------
let chart;
async function fetchHistory() {
  const res = await fetch('/api/history?minutes=180');
  const arr = await res.json();
  const labels = arr.map(p => p.ts.replace('T',' ').slice(0,16));
  const temps  = arr.map(p => p.temp);
  const hums   = arr.map(p => p.hum);

  if (!chart) {
    const ctx = document.getElementById('dhtChart').getContext('2d');
    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Temperatura (°C)', data: temps },
          { label: 'Umidità (%)', data: hums }
        ]
      },
      options: { responsive:true, animation:false, scales:{ y:{ beginAtZero:false } } }
    });
  } else {
    chart.data.labels = labels;
    chart.data.datasets[0].data = temps;
    chart.data.datasets[1].data = hums;
    chart.update();
  }
}
async function tick() {
  await fetchHistory();
  setTimeout(tick, 5000);
}
tick();



