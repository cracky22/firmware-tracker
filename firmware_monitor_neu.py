#!/usr/bin/env python3
"""
file.py
Drei-Modi Firmware-Checker (cli / web / gui) mit Live-Updates.

Install:
    pip install httpx fastapi uvicorn

Start:
    python file.py                # CLI (default)
    python file.py --mode cli
    python file.py --mode web
    python file.py --mode gui
"""

import argparse
import asyncio
import json
import os
import threading
import time
from datetime import datetime
from typing import Dict, Any, List
import xml.etree.ElementTree as ET
import queue
import sys

# Asynchroner HTTP-Client
import httpx

# -------------------------
# Konfiguration & Devices
# -------------------------
FOTA_SERVER_URL = "http://fota-cloud-dn.ospserver.net/firmware"
DEVICES = {
    "Galaxy Buds2 Pro": {
        "model": "SM-R510",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-R510/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-R510/version.test.xml"
        }
    },
    "Galaxy Watch7": {
        "model": "SM-L300",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-L300/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-L300/version.test.xml"
        }
    },
    "Galaxy S24+": {
        "model": "SM-S926B",
        "csc": "EUX",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/EUX/SM-S926B/version.xml",
            "test": f"{FOTA_SERVER_URL}/EUX/SM-S926B/version.test.xml"
        }
    },
    "Galaxy Ring": {
        "model": "SM-Q500",
        "csc": "KOO",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/KOO/SM-Q500/version.xml",
            "test": f"{FOTA_SERVER_URL}/KOO/SM-Q500/version.test.xml"
        }
    },
    "Galaxy Buds3 Pro": {
        "model": "SM-R630",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-R630/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-R630/version.test.xml"
        }
    },
    "Galaxy Watch8": {
        "model": "SM-L320",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-L320/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-L320/version.test.xml"
        }
    },
    "Galaxy S25": {
        "model": "SM-S931B",
        "csc": "EUX",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/EUX/SM-S931B/version.xml",
            "test": f"{FOTA_SERVER_URL}/EUX/SM-S931B/version.test.xml"
        }
    },
    "Galaxy S25 Ultra": {
        "model": "SM-S938B",
        "csc": "EUX",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/EUX/SM-S938B/version.xml",
            "test": f"{FOTA_SERVER_URL}/EUX/SM-S938B/version.test.xml"
        }
    }
}

DATA_DIR = "firmware_data"
os.makedirs(DATA_DIR, exist_ok=True)

# -------------------------
# Hilfsfunktionen
# -------------------------
def format_size(bytes_size: Any) -> str:
    try:
        b = int(bytes_size)
    except Exception:
        return str(bytes_size)
    if b >= 1073741824:
        return f"{b / 1073741824:.2f} GB"
    elif b >= 1048576:
        return f"{b / 1048576:.2f} MB"
    elif b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{b} Bytes"

def load_cached_data(device: Dict[str, Any], firmware_type: str):
    path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_cached_data(device: Dict[str, Any], firmware_type: str, data: Dict[str, Any]):
    path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def compare_versions(old_data: Dict[str, Any], new_data: Dict[str, Any], firmware_type: str) -> List[str]:
    changes = []
    if not old_data:
        changes.append(f"Erste Abfrage für {firmware_type}-Firmware.")
        return changes

    old_latest = old_data.get("latest", "")
    new_latest = new_data.get("latest", "")
    if old_latest != new_latest:
        changes.append(f"Neue {firmware_type} 'latest'-Version: {new_latest} (vorher: {old_latest})")

    old_versions = {v["version"]: v for v in old_data.get("versions", [])}
    new_versions = {v["version"]: v for v in new_data.get("versions", [])}

    for version, vinfo in new_versions.items():
        if version not in old_versions:
            changes.append(f"Neue {firmware_type} Version hinzugefügt: {version} (Größe: {format_size(vinfo.get('fwsize'))})")
        elif old_versions[version].get("fwsize") != vinfo.get("fwsize"):
            changes.append(
                f"{firmware_type} Version {version} geändert: Neue Größe {format_size(vinfo.get('fwsize'))} "
                f"(vorher: {format_size(old_versions[version].get('fwsize'))})"
            )

    for version in old_versions:
        if version not in new_versions:
            changes.append(f"{firmware_type} Version entfernt: {version}")

    return changes

def parse_xml(xml_content: str):
    if not xml_content:
        return None
    try:
        root = ET.fromstring(xml_content)
        firmware_data = {"versions": []}
        latest = root.find(".//latest")
        if latest is not None and latest.text:
            firmware_data["latest"] = latest.text.strip()

        for value in root.findall(".//upgrade/value"):
            version = value.text.strip() if value.text else ""
            rcount = value.get("rcount", "0")
            fwsize = value.get("fwsize", "0")
            firmware_data["versions"].append({
                "version": version,
                "rcount": rcount,
                "fwsize": fwsize
            })
        return firmware_data
    except ET.ParseError:
        return None

# -------------------------
# BROADCAST / EVENT PIPELINE
# -------------------------
# Zentraler Async-Event-Queue, von Checker gefüllt.
# Consumer:
#  - WebSocket-Broadcaster
#  - CLI Printer (synchron)
#  - GUI Poller (thread-safe queue)
EVENT_QUEUE: asyncio.Queue = asyncio.Queue()
# Für WebSocket Clients
WS_CLIENTS: List[Any] = []
# Thread-safe queue für GUI (tkinter)
GUI_QUEUE: "queue.Queue[str]" = queue.Queue()

async def broadcast_event(event: Dict[str, Any]):
    """Event in EVENT_QUEUE legen (async)."""
    await EVENT_QUEUE.put(event)

async def _event_dispatcher_loop():
    """Liest EVENT_QUEUE und verteilt an WebSocket-Clients und GUI-Queue und stdout."""
    while True:
        event = await EVENT_QUEUE.get()
        try:
            text = json.dumps(event, ensure_ascii=False)
        except Exception:
            text = str(event)
        # WebSocket-Clients (async)
        coros = []
        dead = []
        for ws in WS_CLIENTS:
            try:
                coros.append(ws.send_text(text))
            except Exception:
                dead.append(ws)
        if coros:
            # gather but do not fail the loop on exceptions
            await asyncio.gather(*coros, return_exceptions=True)
        # cleanup dead clients
        for d in dead:
            try:
                WS_CLIENTS.remove(d)
            except ValueError:
                pass
        # GUI thread-safe queue
        try:
            GUI_QUEUE.put_nowait(text)
        except Exception:
            pass
        # CLI stdout
        # print human-friendly line if present
        if "log" in event:
            # show short console output
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {event.get('log')}")
        EVENT_QUEUE.task_done()

# -------------------------
# ASYNC CHECKER
# -------------------------
SEM = asyncio.Semaphore(20)  # max parallel requests

async def fetch_xml_async(url: str, client: httpx.AsyncClient, timeout: int = 10) -> str:
    async with SEM:
        try:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            await broadcast_event({"type": "error", "log": f"HTTP error {e.response.status_code} for {url}"})
            return None
        except Exception as e:
            await broadcast_event({"type": "error", "log": f"Request exception for {url}: {e}"})
            return None

async def check_device(device_name: str, device: Dict[str, Any], client: httpx.AsyncClient):
    """Prüft stable & test für ein device, vergleicht, speichert cache und sendet events."""
    out_events = []
    for fw_type, url in device["urls"].items():
        tag = fw_type.lower()
        xml = await fetch_xml_async(url, client)
        if not xml:
            await broadcast_event({"type": "status", "device": device_name, "firmware": tag, "log": f"Keine Daten für {tag}"})
            continue
        new_data = parse_xml(xml)
        if not new_data:
            await broadcast_event({"type": "error", "device": device_name, "firmware": tag, "log": f"XML Parse Error für {tag}"})
            continue
        old_data = load_cached_data(device, tag)
        changes = compare_versions(old_data, new_data, tag.capitalize())
        # save cache
        save_cached_data(device, tag, new_data)
        ts = datetime.now().isoformat()
        if changes:
            for c in changes:
                await broadcast_event({
                    "type": "change",
                    "time": ts,
                    "device": device_name,
                    "firmware": tag,
                    "message": c,
                    "log": f"{device_name} ({tag}) - {c}"
                })
        else:
            await broadcast_event({
                "type": "ok",
                "time": ts,
                "device": device_name,
                "firmware": tag,
                "message": "Keine Änderungen",
                "log": f"{device_name} ({tag}) - keine Änderungen"
            })
        out_events.append({
            "firmware": tag,
            "latest": new_data.get("latest", ""),
            "versions": new_data.get("versions", [])
        })
    # send device summary
    await broadcast_event({
        "type": "device_summary",
        "time": datetime.now().isoformat(),
        "device": device_name,
        "summary": out_events
    })

async def run_full_check(loop_delay: int = 0):
    """Führt Checks für alle Geräte aus (einmalig)."""
    async with httpx.AsyncClient() as client:
        tasks = [check_device(name, dev, client) for name, dev in DEVICES.items()]
        await asyncio.gather(*tasks, return_exceptions=True)
    if loop_delay:
        await asyncio.sleep(loop_delay)

async def periodic_worker(interval_seconds: int = 60):
    """Periodischer Worker: führt Checks zyklisch aus und sendet Log-Events."""
    await broadcast_event({"type": "log", "log": f"Starte periodic worker ({interval_seconds}s)..."})
    while True:
        await broadcast_event({"type": "log", "log": f"Starte Check-Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"})
        try:
            await run_full_check()
        except Exception as e:
            await broadcast_event({"type": "error", "log": f"Fehler beim Run: {e}"})
        await asyncio.sleep(interval_seconds)

# -------------------------
# WEB SERVER (FastAPI + WebSocket)
# -------------------------
def start_web_server(host: str = "127.0.0.1", port: int = 8000, auto_period: int = 120):
    """
    Startet UVicorn / FastAPI in diesem Prozess.
    Die Async-Eventloop kommt aus asyncio (dispatcher + checker).
    """
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    import uvicorn

    app = FastAPI()

    # Einfaches, grafisches Web-Interface - JS aktualisiert Tabelle via WebSocket
    INDEX_HTML = r"""
    <!doctype html>
    <html lang="de">
    <head>
      <meta charset="utf-8"/>
      <title>Firmware Live Dashboard</title>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <style>
        body { font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 0; background:#f4f7fb; color:#222; }
        header { padding: 12px 20px; background: #1f6feb; color: white; }
        .container { display: grid; grid-template-columns: 340px 1fr; gap: 12px; padding: 12px; }
        .card { background: white; border-radius: 8px; padding: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
        #deviceList { height: calc(100vh - 160px); overflow:auto; }
        .device { padding: 8px; border-bottom: 1px solid #eee; cursor: pointer; }
        .device:hover { background:#f0f6ff; }
        .device .title { font-weight:600; }
        .device .meta { font-size:12px; color:#666; }
        #details { padding:8px; }
        #log { height: 220px; overflow:auto; background:#0b1220; color:#b8f2c2; padding:8px; border-radius:6px; font-family: monospace; font-size:12px; }
        .badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; background:#eef2ff; color:#1f3a93; margin-right:6px; }
        table { width:100%; border-collapse: collapse; }
        th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #f2f5fb; }
      </style>
    </head>
    <body>
      <header><h2>Firmware Live Dashboard</h2></header>
      <div class="container">
        <div class="card" id="left">
          <h3>Geräte</h3>
          <div id="deviceList"></div>
        </div>
        <div class="card">
          <div id="details">
            <h3 id="devName">Wähle ein Gerät</h3>
            <div id="devMeta"></div>
            <h4>Firmware</h4>
            <table id="fwTable"><thead><tr><th>Type</th><th>Latest</th><th>Info</th></tr></thead><tbody></tbody></table>
            <h4>Live-Log</h4>
            <div id="log"></div>
          </div>
        </div>
      </div>

      <script>
        const ws = new WebSocket("ws://" + location.host + "/ws");
        let devices = {}; // deviceName -> last summary
        let selected = null;
        const deviceListEl = document.getElementById("deviceList");
        const logEl = document.getElementById("log");
        const devName = document.getElementById("devName");
        const devMeta = document.getElementById("devMeta");
        const fwTableBody = document.querySelector("#fwTable tbody");

        function renderDeviceList() {
            deviceListEl.innerHTML = "";
            Object.keys(devices).sort().forEach(name => {
                const d = devices[name];
                const el = document.createElement("div");
                el.className = "device";
                el.onclick = () => { selected = name; renderDetails(); };
                el.innerHTML = `<div class="title">${name}</div><div class="meta">letzter Check: ${d.time || "-"}</div>`;
                deviceListEl.appendChild(el);
            });
        }

        function renderDetails() {
            if(!selected) return;
            const info = devices[selected];
            devName.innerText = selected;
            devMeta.innerHTML = `<div class="badge">Model: ${info.model || "-"}</div><div class="badge">CSC: ${info.csc || "-"}</div>`;
            fwTableBody.innerHTML = "";
            (info.summary || []).forEach(s => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td>${s.firmware}</td><td>${s.latest || "-"}</td><td>${(s.versions||[]).length} Versionen</td>`;
                fwTableBody.appendChild(tr);
            });
        }

        ws.onmessage = (e) => {
            try {
                const obj = JSON.parse(e.data);
                // append logs
                if(obj.log){
                    const line = document.createElement("div");
                    line.innerText = `[${new Date().toLocaleTimeString()}] ${obj.log}`;
                    logEl.appendChild(line);
                    logEl.scrollTop = logEl.scrollHeight;
                }
                if(obj.type === "device_summary"){
                    devices[obj.device] = devices[obj.device] || {};
                    devices[obj.device].summary = obj.summary;
                    devices[obj.device].time = obj.time;
                    // keep model/csc if previously known
                    if(obj.model) devices[obj.device].model = obj.model;
                    if(obj.csc) devices[obj.device].csc = obj.csc;
                    // render list
                    renderDeviceList();
                    if(!selected) selected = Object.keys(devices)[0];
                    renderDetails();
                }
                if(obj.type === "change"){
                    const line = document.createElement("div");
                    line.innerText = `[CHANGE] ${obj.device} (${obj.firmware}) — ${obj.message}`;
                    line.style.color = "#b50000";
                    logEl.appendChild(line);
                    logEl.scrollTop = logEl.scrollHeight;
                }
            } catch (e){
                console.warn("WS parse error", e);
            }
        };

        ws.onopen = () => {
            console.log("WebSocket offen");
        };
        ws.onclose = () => {
            const line = document.createElement("div");
            line.innerText = "WebSocket geschlossen";
            logEl.appendChild(line);
        };
      </script>
    </body>
    </html>
    """

    @app.get("/")
    async def index():
        return HTMLResponse(INDEX_HTML)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        # registrieren
        WS_CLIENTS.append(ws)
        try:
            # send initial state (read caches)
            for name, device in DEVICES.items():
                model = device.get("model")
                csc = device.get("csc")
                summary = []
                for fw_type in device["urls"].keys():
                    cached = load_cached_data(device, fw_type)
                    summary.append({
                        "firmware": fw_type,
                        "latest": (cached.get("latest") if cached else ""),
                        "versions": (cached.get("versions") if cached else [])
                    })
                await ws.send_text(json.dumps({
                    "type": "device_summary",
                    "device": name,
                    "time": datetime.now().isoformat(),
                    "model": model, "csc": csc, "summary": summary
                }, ensure_ascii=False))
            # keep connection open; server sends updates via global dispatcher
            while True:
                # keep alive read (clients may send pings)
                try:
                    await ws.receive_text()
                except Exception:
                    # ignore, continue loop; if client disconnects we'll catch on send
                    await asyncio.sleep(1)
        except Exception:
            # disconnect
            pass
        finally:
            try:
                WS_CLIENTS.remove(ws)
            except ValueError:
                pass

    # Starte background loop: dispatcher + periodic worker
    async def _serve_background():
        # dispatcher (task)
        asyncio.create_task(_event_dispatcher_loop())
        # initial run once
        await run_full_check()
        # periodic worker alle x sekunden
        asyncio.create_task(periodic_worker(auto_period))
        # keep running forever
        while True:
            await asyncio.sleep(3600)

    # run uvicorn in the current thread
    # but ensure the background tasks run in the same loop: use uvicorn.run with loop='asyncio' keeps same thread
    def _run_uvicorn():
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        # run server and background in same loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.create_task(_serve_background())
        loop.run_until_complete(server.serve())

    print(f"Starte Web-Server auf http://{host}:{port} ...")
    _run_uvicorn()

# -------------------------
# GUI (Tkinter)
# -------------------------
def start_gui(periodic_seconds: int = 120):
    """
    Startet ein Tkinter-Fenster und konsumiert GUI_QUEUE für Live-Updates.
    Die Async-Checker läuft in einem separaten asyncio-Thread.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as e:
        print("Tkinter nicht verfügbar:", e)
        return

    root = tk.Tk()
    root.title("Firmware Live (GUI)")

    root.geometry("900x600")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    frame = ttk.Frame(root, padding=8)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(0, weight=1)

    # Device list
    device_list = tk.Listbox(frame, width=36)
    device_list.grid(row=0, column=0, sticky="ns")
    # Details area (Treeview)
    cols = ("firmware", "latest", "versions")
    tree = ttk.Treeview(frame, columns=cols, show="headings")
    for c in cols:
        tree.heading(c, text=c)
    tree.grid(row=0, column=1, sticky="nsew")

    # Log
    logbox = tk.Text(frame, height=12, bg="#0b1220", fg="#b8f2c2", font=("Consolas", 10))
    logbox.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8,0))

    devices_state = {}

    def on_select(evt):
        w = evt.widget
        if not w.curselection():
            return
        idx = int(w.curselection()[0])
        name = w.get(idx)
        selected = devices_state.get(name, {})
        # update tree
        for r in tree.get_children():
            tree.delete(r)
        for s in selected.get("summary", []):
            tree.insert("", "end", values=(s.get("firmware"), s.get("latest"), len(s.get("versions", []))))

    device_list.bind("<<ListboxSelect>>", on_select)

    # updater: liest GUI_QUEUE (thread-safe)
    def gui_poller():
        try:
            while True:
                item = GUI_QUEUE.get_nowait()
                try:
                    obj = json.loads(item)
                except Exception:
                    logbox.insert("end", item + "\n")
                    logbox.see("end")
                    continue
                # handle different events
                if obj.get("type") == "device_summary":
                    name = obj.get("device")
                    devices_state[name] = {
                        "summary": obj.get("summary", []),
                        "time": obj.get("time"),
                        "model": obj.get("model"),
                        "csc": obj.get("csc")
                    }
                    # refresh listbox content
                    device_list.delete(0, "end")
                    for k in sorted(devices_state.keys()):
                        device_list.insert("end", k)
                elif obj.get("type") == "log" or obj.get("log"):
                    logbox.insert("end", f"[LOG] {obj.get('log')}\n")
                    logbox.see("end")
                elif obj.get("type") == "change":
                    logbox.insert("end", f"[CHANGE] {obj.get('device')} {obj.get('firmware')}: {obj.get('message')}\n")
                    logbox.see("end")
                elif obj.get("type") == "error":
                    logbox.insert("end", f"[ERROR] {obj.get('log')}\n")
                    logbox.see("end")
        except queue.Empty:
            pass
        # schedule next poll
        root.after(250, gui_poller)

    # Start asyncio background loop in a thread
    def start_async_loop_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # dispatcher
        loop.create_task(_event_dispatcher_loop())
        # initial run and periodic worker
        loop.create_task(run_full_check())
        loop.create_task(periodic_worker(periodic_seconds))
        try:
            loop.run_forever()
        except Exception:
            pass

    t = threading.Thread(target=start_async_loop_in_thread, daemon=True)
    t.start()

    root.after(200, gui_poller)
    root.mainloop()

# -------------------------
# CLI Mode
# -------------------------
def start_cli(single_run: bool = True, periodic_seconds: int = 120):
    """
    CLI: startet event dispatcher + checker; gibt Logs in stdout aus.
    Wenn single_run == False, dann läuft periodic_worker.
    """
    async def _cli_main():
        # start dispatcher
        dispatcher_task = asyncio.create_task(_event_dispatcher_loop())
        # initial run
        await run_full_check()
        if single_run:
            # small grace time then stop
            await asyncio.sleep(1)
            # cancel dispatcher
            dispatcher_task.cancel()
            return
        else:
            # periodic worker
            await periodic_worker(periodic_seconds)

    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("Abbruch per Strg-C.")
    except Exception as e:
        print("Fehler in CLI:", e)

# -------------------------
# ARGPARSE & START
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="Firmware-Checker mit CLI / Web / GUI Modus")
    parser.add_argument("--mode", choices=["cli", "web", "gui"], default="cli", help="Startmodus")
    parser.add_argument("--period", type=int, default=120, help="Polling-Intervall in Sekunden (web/gui/cli periodic)")
    parser.add_argument("--single", action="store_true", help="Nur einmal prüfen (nur relevant für cli)")
    args = parser.parse_args()

    if args.mode == "cli":
        # CLI: synchroner entrypoint
        print(f"Starte CLI-Modus (einmalig={args.single})")
        start_cli(single_run=args.single, periodic_seconds=args.period)

    elif args.mode == "web":
        # Web: run FastAPI + background async tasks
        start_web_server(auto_period=args.period)

    elif args.mode == "gui":
        # GUI: Tkinter window
        start_gui(periodic_seconds=args.period)

if __name__ == "__main__":
    main()
