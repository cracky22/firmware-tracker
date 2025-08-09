# Samsung Firmware Tracker

Dieses Python-Skript überprüft regelmäßig Firmware-Versionen für ausgewählte Samsung-Geräte, vergleicht sie mit zuvor gespeicherten Daten und listet Änderungen auf.  
Die Daten werden aus dem offiziellen FOTA-Server (Firmware Over-The-Air) von Samsung abgerufen.

## Funktionen

- **Geräteunterstützung**: Vordefinierte Modelle (z. B. Galaxy Watch7, S24+, Watch8, S25) mit Modellnummern und CSC-Codes.
- **Zwei Firmware-Kanäle**:
  - **stable**: Offiziell veröffentlichte Firmware
  - **test**: Test-/Beta-Firmware
- **Vergleich mit Cache**:
  - Neue Versionen erkennen
  - Geänderte Firmwaregrößen melden
  - Entfernte Versionen auflisten
- **Ausgabe in der Konsole** mit Datum und Uhrzeit
- **Persistente Speicherung** der zuletzt bekannten Versionen als JSON-Dateien

## Voraussetzungen

- Python 3.7+
- Abhängigkeiten:
  ```bash
  pip install requests
