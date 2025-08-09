import xml.etree.ElementTree as ET
from datetime import datetime
import requests
import json
import os

FOTA_SERVER_URL = "http://fota-cloud-dn.ospserver.net/firmware"
DEVICES = {
    "Galaxy Watch7": {
        "model": "SM-L310",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-L310/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-L310/version.test.xml"
        }
    },
    "Galaxy S24+": {
        "model": "SM-S926B",
        "csc": "EUX",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/EUX/SM-S926B/version.xml",
            "test": f"{FOTA_SERVER_URL}/EUX/SM-S926B/version.test.xml"
        }
    }
}

DATA_DIR = "firmware_data"
os.makedirs(DATA_DIR, exist_ok=True)

def format_size(bytes_size):
    bytes_size = int(bytes_size)
    if bytes_size >= 1073741824:
        return f"{bytes_size / 1073741824:.2f} GB"
    elif bytes_size >= 1048576:
        return f"{bytes_size / 1048576:.2f} MB"
    elif bytes_size >= 1024:
        return f"{bytes_size / 1024:.2f} KB"
    return f"{bytes_size} Bytes"

def fetch_xml(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Request Exception at {url}: {e}")
        return None

def parse_xml(xml_content):
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
    except ET.ParseError as e:
        print(f"XML ParseError: {e}")
        return None

def load_cached_data(device, firmware_type):
    file_path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return None

def save_cached_data(device, firmware_type, data):
    file_path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

def compare_versions(old_data, new_data, firmware_type):
    changes = []
    if not old_data:
        return [f"Erste Abfrage für {firmware_type}-Firmware."]

    old_latest = old_data.get("latest", "")
    new_latest = new_data.get("latest", "")
    if old_latest != new_latest:
        changes.append(f"Neue {firmware_type} 'latest'-Version: {new_latest} (vorher: {old_latest})")

    old_versions = {v["version"]: v for v in old_data.get("versions", [])}
    new_versions = {v["version"]: v for v in new_data.get("versions", [])}

    for version in new_versions:
        if version not in old_versions:
            changes.append(f"Neue {firmware_type} Version hinzugefügt: {version} (Größe: {format_size(new_versions[version]['fwsize'])})")
        elif old_versions[version]["fwsize"] != new_versions[version]["fwsize"]:
            changes.append(f"{firmware_type} Version {version} geändert: Neue Größe {format_size(new_versions[version]['fwsize'])} (vorher: {format_size(old_versions[version]['fwsize'])})")

    for version in old_versions:
        if version not in new_versions:
            changes.append(f"{firmware_type} Version entfernt: {version}")

    return changes

def main():
    print(f"Firmware-Tracking gestartet: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    for device_name, device in DEVICES.items():
        print(f"Überprüfe {device_name} ({device['model']}, CSC: {device['csc']})")
        
        for firmware_type, url in device["urls"].items():
            xml_content = fetch_xml(url)
            if not xml_content:
                print(f"Keine Daten für {firmware_type}-Firmware abgerufen.")
                continue
            
            new_data = parse_xml(xml_content)
            if not new_data:
                print(f"Fehler beim Parsen der {firmware_type}-Firmware-Daten.")
                continue
            
            old_data = load_cached_data(device, firmware_type)
            changes = compare_versions(old_data, new_data, firmware_type.capitalize())
            
            if changes:
                print(f"\nÄnderungen für {device_name} ({firmware_type}-Firmware):")
                for change in changes:
                    print(f" - {change}")
            else:
                print(f"Keine Änderungen für {firmware_type}-Firmware.")
            
            save_cached_data(device, firmware_type, new_data)
        
        print("-" * 50)

if __name__ == "__main__":
    main()