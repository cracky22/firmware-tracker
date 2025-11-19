#!/usr/bin/env python3
"""
Samsung Firmware Live Dashboard – FINAL FIX
Läuft sofort, inkl. Migration deiner alten devices.json
"""

import argparse
import asyncio
import json
import os
import queue
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List

import httpx
import xml.etree.ElementTree as ET

# -------------------------
# Pfade
# -------------------------
DATA_DIR = "firmware_data"
DEVICES_FILE = "devices.json"
os.makedirs(DATA_DIR, exist_ok=True)

# -------------------------
# Devices JSON + Migration
# -------------------------
_devices_lock = asyncio.Lock()

def _migrate_old_format(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and "devices" not in data:
        print("Migrating alte devices.json (dict → list mit IDs)…")
        new_list = []
        for name, info in data.items():
            new_list.append({
                "id": str(uuid.uuid4()),
                "name": name,
                "model": info.get("model", ""),
                "csc": info.get("csc", ""),
                "favorite": info.get("favorite", False),
                "urls": info.get("urls", generate_urls_for_device(info.get("model", ""), info.get("csc", "")))
            })
        return new_list
    return data.get("devices", [])

def atomic_write_json(path: str, data: Dict[str, Any]):
    dirn = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="." + os.path.basename(path), dir=dirn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except:
            pass
        raise

def load_devices_sync() -> List[Dict[str, Any]]:
    if not os.path.exists(DEVICES_FILE):
        atomic_write_json(DEVICES_FILE, {"devices": []})
        return []

    with open(DEVICES_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    devices = _migrate_old_format(raw)

    changed = False
    for d in devices:
        if "id" not in d:
            d["id"] = str(uuid.uuid4())
            changed = True
    if changed:
        atomic_write_json(DEVICES_FILE, {"devices": devices})

    return devices

async def load_devices() -> List[Dict[str, Any]]:
    async with _devices_lock:
        return load_devices_sync()

async def save_devices(devices: List[Dict[str, Any]]):
    async with _devices_lock:
        atomic_write_json(DEVICES_FILE, {"devices": devices})

def generate_urls_for_device(model: str, csc: str) -> Dict[str, str]:
    if not model or not csc:
        return {}
    base = "http://fota-cloud-dn.ospserver.net/firmware"
    return {
        "stable": f"{base}/{csc}/{model}/version.xml",
        "test": f"{base}/{csc}/{model}/version.test.xml"
    }

# -------------------------
# Cache & XML
# -------------------------
def cache_path_for(device: Dict[str, Any], fw_type: str) -> str:
    return os.path.join(DATA_DIR, f"{device.get('model', 'unknown')}_{fw_type}.json")

def load_cached_data(device: Dict[str, Any], fw_type: str) -> Dict | None:
    path = cache_path_for(device, fw_type)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None
    return None

def save_cached_data(device: Dict[str, Any], fw_type: str, data: Dict):
    path = cache_path_for(device, fw_type)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_xml(xml_content: str) -> Dict | None:
    if not xml_content:
        return None
    try:
        root = ET.fromstring(xml_content)
        data = {"versions": []}
        latest = root.find(".//latest")
        if latest is not None and latest.text:
            data["latest"] = latest.text.strip()

        for v in root.findall(".//upgrade/value"):
            data["versions"].append({
                "version": v.text.strip() if v.text else "",
                "rcount": v.get("rcount", "0"),
                "fwsize": v.get("fwsize", "0")
            })
        return data
    except:
        return None

# -------------------------
# Events
# -------------------------
EVENT_QUEUE: asyncio.Queue = asyncio.Queue()
WS_CLIENTS: List = []
GUI_QUEUE = queue.Queue()

async def broadcast_event(event: Dict):
    await EVENT_QUEUE.put(event)

async def _event_dispatcher_loop():
    while True:
        event = await EVENT_QUEUE.get()
        text = json.dumps(event, ensure_ascii=False)

        # WebSocket
        dead = []
        for ws in WS_CLIENTS[:]:
            try:
                await ws.send_text(text)
            except:
                dead.append(ws)
        for d in dead:
            WS_CLIENTS.remove(d)

        # GUI
        try:
            GUI_QUEUE.put_nowait(text)
        except:
            pass

        # Konsole
        if event.get("log"):
            print(f"[{datetime.now():%H:%M:%S}] {event['log']}")

        EVENT_QUEUE.task_done()

# -------------------------
# Checker – jetzt OHNE client-Parameter (eigener Client intern)
# -------------------------
SEM = asyncio.Semaphore(20)

async def fetch_xml(url: str) -> str | None:
    async with SEM:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url)
                r.raise_for_status()
                return r.text
        except Exception as e:
            await broadcast_event({"type": "error", "log": f"Fetch-Fehler {url}: {e}"})
            return None

async def check_device(device: Dict):  # ← nur noch 1 Parameter!
    dev_id = device["id"]
    name = device.get("name", device.get("model", "Unknown"))
    summary = []

    for fw_type, url in device.get("urls", {}).items():
        xml = await fetch_xml(url)
        if not xml:
            summary.append({"firmware": fw_type, "latest": "-", "versions": []})
            continue

        new_data = parse_xml(xml)
        if not new_data:
            summary.append({"firmware": fw_type, "latest": "(Parse-Fehler)", "versions": []})
            continue

        old = load_cached_data(device, fw_type)
        if old and old.get("latest") != new_data.get("latest"):
            await broadcast_event({
                "type": "change",
                "device": name,
                "firmware": fw_type,
                "message": f"Neue Version: {new_data['latest']}",
                "log": f"UPDATE → {name} ({fw_type}) → {new_data['latest']}"
            })

        save_cached_data(device, fw_type, new_data)
        summary.append({
            "firmware": fw_type,
            "latest": new_data.get("latest", "-"),
            "versions": new_data.get("versions", [])
        })

    await broadcast_event({
        "type": "device_summary",
        "id": dev_id,
        "device": name,
        "model": device.get("model"),
        "csc": device.get("csc"),
        "favorite": device.get("favorite", False),
        "summary": summary
    })

async def run_full_check():
    devs = await load_devices()
    if not devs:
        await broadcast_event({"type": "log", "log": "Keine Geräte gefunden"})
        return
    # ← jetzt ohne client übergeben!
    await asyncio.gather(*(check_device(d) for d in devs))

async def periodic_worker(interval: int = 120):
    await broadcast_event({"type": "log", "log": f"Periodic check alle {interval}s"})
    while True:
        await run_full_check()
        await asyncio.sleep(interval)

# -------------------------
# Web Server (unverändert – nur run_full_check korrigiert oben)
# -------------------------
def start_web_server(host="0.0.0.0", port=8000, period=120):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.responses import HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    INDEX_HTML = """<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><title>Samsung Firmware Dashboard</title>
<style>
  body{font-family:Arial,sans-serif;margin:0;background:#f5f7fa;color:#333}
  header{background:#0d6efd;color:white;padding:15px;text-align:center}
  .c{display:grid;grid-template-columns:380px 1fr;gap:20px;padding:20px}
  .card{background:white;border-radius:10px;padding:20px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
  #list{max-height:70vh;overflow:auto}
  .dev{padding:12px;border-bottom:1px solid #eee;cursor:pointer}
  .dev:hover{background:#ebf3ff}
  .fav{color:#ff9800;font-size:1.4em;margin-right:8px}
  #log{height:220px;overflow:auto;background:#1e1e1e;color:#9f9;padding:10px;font-family:monospace;font-size:0.9em;border-radius:8px}
  input{width:100%;padding:8px;margin:5px 0;box-sizing:border-box}
  button{padding:8px 12px;background:#0d6efd;color:white;border:none;border-radius:6px;cursor:pointer}
  button.danger{background:#d32f2f}
  .row{display:flex;gap:10px}
  table{width:100%;border-collapse:collapse;margin-top:15px}
  th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left}
</style>
</head>
<body>
<header><h2>Samsung Firmware Live Dashboard</h2></header>
<div class="c">
  <div class="card">
    <h3>Geräte</h3>
    <div id="list"></div>
    <hr>
    <h4>Neues Gerät</h4>
    <input placeholder="Name" id="n"><input placeholder="Model" id="m"><input placeholder="CSC" id="c">
    <label><input type="checkbox" id="f"> Favorit</label>
    <div class="row"><button onclick="add()">Hinzufügen</button><button onclick="load()">Refresh</button></div>
  </div>
  <div class="card">
    <h3 id="title">Gerät wählen</h3>
    <div id="meta"></div>
    <table><thead><tr><th>Typ</th><th>Latest</th><th>Versionen</th></tr></thead><tbody id="tb"></tbody></table>
    <h4>Bearbeiten</h4>
    <input id="eid" type="hidden"><input id="en"><input id="em"><input id="ec">
    <label><input type="checkbox" id="ef"> Favorit</label>
    <div class="row"><button onclick="save()">Speichern</button><button class="danger" onclick="del()">Löschen</button></div>
    <h4>Live-Log</h4>
    <div id="log"></div>
  </div>
</div>

<script>
  const ws = new WebSocket(`ws://${location.host}/ws`);
  let devs = {}, sel = null;

  function render(){
    const l = document.getElementById('list'); l.innerHTML='';
    Object.values(devs).sort((a,b)=> (b.favorite-a.favorite)||a.name.localeCompare(b.name)).forEach(d=>{
      const e=document.createElement('div');e.className='dev';
      e.innerHTML = (d.favorite?'<span class="fav">★</span>':'') + `<b>${d.name}</b><br><small>${d.model} • ${d.csc}</small>`;
      e.onclick=()=>{sel=d.id; renderDetail(d);};
      l.appendChild(e);
    });
  }

  function renderDetail(d){
    document.getElementById('title').textContent = d.name || "Gerät wählen";
    document.getElementById('meta').innerHTML = `<i>Model: ${d.model} | CSC: ${d.csc}</i>`;
    const tb = document.getElementById('tb'); tb.innerHTML='';
    (d.summary||[]).forEach(s=>{
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${s.firmware}</td><td>${s.latest}</td><td>${s.versions?.length||0}</td>`;
      tb.appendChild(tr);
    });
    document.getElementById('eid').value = d.id || '';
    document.getElementById('en').value = d.name || '';
    document.getElementById('em').value = d.model || '';
    document.getElementById('ec').value = d.csc || '';
    document.getElementById('ef').checked = !!d.favorite;
  }

  ws.onmessage = e=>{
    try{
      const o = JSON.parse(e.data);
      if(o.type==='device_summary' && o.id){
        devs[o.id] = o;
        render();
        if(sel===o.id) renderDetail(o);
      }
      if(o.log){
        const l=document.createElement('div');
        l.textContent=`[${new Date().toLocaleTimeString()}] ${o.log}`;
        if(o.type==='change') l.style.color='#d50000';
        document.getElementById('log').appendChild(l);
        document.getElementById('log').scrollTop = 1e9;
      }
      if(o.type==='devices_changed') load();
    }catch(e){console.error(e)}
  };

  async function load(){
    const r = await fetch('/api/devices');
    const a = await r.json();
    a.forEach(d=>devs[d.id]=d);
    render();
    if(!sel && Object.keys(devs).length) { sel = Object.keys(devs)[0]; renderDetail(devs[sel]); }
  }

  async function add(){
    const p = {name:document.getElementById('n').value.trim(), model:document.getElementById('m').value.trim().toUpperCase(), csc:document.getElementById('c').value.trim().toUpperCase(), favorite:document.getElementById('f').checked};
    if(!p.model || !p.csc) return alert("Model + CSC erforderlich");
    await fetch('/api/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    document.getElementById('n').value=document.getElementById('m').value=document.getElementById('c').value='';
    load();
  }

  async function save(){
    const id = document.getElementById('eid').value;
    const p = {name:document.getElementById('en').value.trim(), model:document.getElementById('em').value.trim().toUpperCase(), csc:document.getElementById('ec').value.trim().toUpperCase(), favorite:document.getElementById('ef').checked};
    await fetch(`/api/devices/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    load();
  }

  async function del(){
    if(!confirm('Wirklich löschen?')) return;
    await fetch(`/api/devices/${document.getElementById('eid').value}`,{method:'DELETE'});
    load();
  }

  ws.onopen = load;
</script>
</body>
</html>"""

    # API Endpoints (unverändert – funktionieren perfekt)
    @app.get("/api/devices")
    async def api_list(): return await load_devices()

    @app.post("/api/devices")
    async def api_add(dev: dict):
        devs = await load_devices()
        new = {
            "id": str(uuid.uuid4()),
            "name": dev.get("name", "Unnamed"),
            "model": dev.get("model", "").strip().upper(),
            "csc": dev.get("csc", "").strip().upper(),
            "favorite": bool(dev.get("favorite")),
            "urls": generate_urls_for_device(dev.get("model"), dev.get("csc"))
        }
        if not new["urls"]:
            raise HTTPException(400, "Model und CSC erforderlich")
        devs.append(new)
        await save_devices(devs)
        await broadcast_event({"type": "devices_changed", "log": f"Gerät hinzugefügt: {new['name']}"})
        return new

    @app.put("/api/devices/{dev_id}")
    async def api_update(dev_id: str, payload: dict):
        devs = await load_devices()
        for d in devs:
            if d["id"] == dev_id:
                d.update({
                    "name": payload.get("name", d["name"]),
                    "model": payload.get("model", d["model"]).strip().upper(),
                    "csc": payload.get("csc", d["csc"]).strip().upper(),
                    "favorite": bool(payload.get("favorite", d.get("favorite"))),
                })
                d["urls"] = generate_urls_for_device(d["model"], d["csc"])
                await save_devices(devs)
                await broadcast_event({"type": "devices_changed", "log": f"Gerät aktualisiert: {d['name']}"})
                return d
        raise HTTPException(404)

    @app.delete("/api/devices/{dev_id}")
    async def api_delete(dev_id: str):
        devs = await load_devices()
        removed = next((d for d in devs if d["id"] == dev_id), None)
        if not removed: raise HTTPException(404)
        devs = [d for d in devs if d["id"] != dev_id]
        await save_devices(devs)
        await broadcast_event({"type": "devices_changed", "log": f"Gerät gelöscht: {removed['name']}"})
        return {"ok": True}

    @app.get("/")
    async def root():
        return HTMLResponse(INDEX_HTML)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        WS_CLIENTS.append(ws)
        try:
            for d in await load_devices():
                sumry = []
                for t in d.get("urls", {}):
                    cached = load_cached_data(d, t)
                    sumry.append({
                        "firmware": t,
                        "latest": cached.get("latest", "-") if cached else "-",
                        "versions": cached.get("versions", []) if cached else []
                    })
                await ws.send_text(json.dumps({
                    "type": "device_summary",
                    "id": d["id"],
                    "device": d.get("name"),
                    "model": d.get("model"),
                    "csc": d.get("csc"),
                    "favorite": d.get("favorite", False),
                    "summary": sumry
                }, ensure_ascii=False))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            WS_CLIENTS.remove(ws)
        except:
            if ws in WS_CLIENTS: WS_CLIENTS.remove(ws)

    async def startup():
        asyncio.create_task(_event_dispatcher_loop())
        await asyncio.sleep(0.5)
        await run_full_check()          # ← jetzt korrekt!
        asyncio.create_task(periodic_worker(period))

    app.add_event_handler("startup", startup)

    print(f"\nDashboard läuft → http://127.0.0.1:{port} (oder http://localhost:{port})\n")
    uvicorn.run(app, host=host, port=port)

# -------------------------
# CLI
# -------------------------
async def cli_main():
    asyncio.create_task(_event_dispatcher_loop())
    await run_full_check()
    await periodic_worker(120)

def start_cli():
    asyncio.run(cli_main())

# -------------------------
# Entry
# -------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["web", "cli"], default="web")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--period", type=int, default=120)
    args = parser.parse_args()

    if args.mode == "web":
        start_web_server(port=args.port, period=args.period)
    else:
        start_cli()