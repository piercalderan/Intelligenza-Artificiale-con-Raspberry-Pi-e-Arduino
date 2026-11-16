let chart, dataT=[], dataH=[], labels=[];

async function fetchLatest(){
  const r = await fetch('/api/latest'); const j = await r.json();
  document.getElementById('temp').textContent = j.temp ?? '--';
  document.getElementById('hum').textContent  = j.hum  ?? '--';
  document.getElementById('ts').textContent   = j.ts   ?? '--';
}

async function fetchHistory(){
  const r = await fetch('/api/history?n=200'); const rows = await r.json();
  labels = rows.map(r=>r.timestamp_iso);
  dataT  = rows.map(r=>parseFloat(r.temp));
  dataH  = rows.map(r=>parseFloat(r.hum));
  draw();
}

function draw(){
  const ctx = document.getElementById('c').getContext('2d');
  chart && chart.destroy();
  chart = new Chart(ctx,{
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'Temp °C', data:dataT},
        {label:'Umidità %', data:dataH}
      ]
    },
    options:{responsive:true, animation:false}
  });
}

async function say(){
  const t = `La temperatura è di ${document.getElementById('temp').textContent} gradi e l'umidità è ${document.getElementById('hum').textContent} percento.`;
  const r = await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const player = document.getElementById('player');
  player.src = url; player.play();
}

async function led(action){
  await fetch('/api/arduino/led',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
}

setInterval(fetchLatest, 2000);
fetchLatest(); fetchHistory();