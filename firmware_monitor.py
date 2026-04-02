import xml.etree.ElementTree as ET
from datetime import datetime
import httpx
import json
import os
import asyncio

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
    "Galaxy Watch7 Korea": {
        "model": "SM-L300",
        "csc": "KOO",
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
    "Galaxy Buds4 Pro": {
        "model": "SM-R640",
        "csc": "DBT",
        "urls": {
            "stable": f"{FOTA_SERVER_URL}/DBT/SM-R640/version.xml",
            "test": f"{FOTA_SERVER_URL}/DBT/SM-R640/version.test.xml"
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


def format_size(bytes_size):
    bytes_size = int(bytes_size)
    if bytes_size >= 1073741824:
        return f"{bytes_size / 1073741824:.2f} GB"
    elif bytes_size >= 1048576:
        return f"{bytes_size / 1048576:.2f} MB"
    elif bytes_size >= 1024:
        return f"{bytes_size / 1024:.2f} KB"
    return f"{bytes_size} Bytes"


async def fetch_xml(url, client):
    try:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        return resp.text
    except Exception:
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
    except ET.ParseError:
        return None


def load_cached_data(device, firmware_type):
    path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def save_cached_data(device, firmware_type, data):
    path = os.path.join(DATA_DIR, f"{device['model']}_{firmware_type}.json")
    with open(path, "w") as f:
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
            changes.append(
                f"Neue {firmware_type} Version hinzugefügt: {version} "
                f"(Größe: {format_size(new_versions[version]['fwsize'])})"
            )
        elif old_versions[version]["fwsize"] != new_versions[version]["fwsize"]:
            changes.append(
                f"{firmware_type} Version {version} geändert: Neue Größe "
                f"{format_size(new_versions[version]['fwsize'])} "
                f"(vorher: {format_size(old_versions[version]['fwsize'])})"
            )

    for version in old_versions:
        if version not in new_versions:
            changes.append(f"{firmware_type} Version entfernt: {version}")

    return changes


async def process_device(device_name, device, client):
    output = []
    output.append(f"Überprüfe {device_name} ({device['model']}, CSC: {device['csc']})")

    for firmware_type, url in device["urls"].items():
        xml = await fetch_xml(url, client)

        if not xml:
            output.append(f"Keine Daten für {firmware_type}-Firmware.")
            continue

        new_data = parse_xml(xml)
        if not new_data:
            output.append(f"Fehler beim Parsen der {firmware_type}-Firmware.")
            continue

        old_data = load_cached_data(device, firmware_type)
        changes = compare_versions(old_data, new_data, firmware_type.capitalize())

        if changes:
            output.append(f"\nÄnderungen für {device_name} ({firmware_type}-Firmware):")
            for c in changes:
                output.append(f" - {c}")
        else:
            output.append(f"Keine Änderungen für {firmware_type}-Firmware.")

        save_cached_data(device, firmware_type, new_data)

    output.append("-" * 50)
    return "\n".join(output)


async def main():
    print(f"Firmware-Tracking gestartet: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    async with httpx.AsyncClient() as client:
        tasks = [
            process_device(name, device, client)
            for name, device in DEVICES.items()
        ]

        results = await asyncio.gather(*tasks)

        for r in results:
            print(r)


if __name__ == "__main__":
    asyncio.run(main())
