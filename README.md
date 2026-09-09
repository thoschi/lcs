# LCS v0.6

**Linuxmuster Client/Core/Connection Services**

v0.6 trennt das Quellrepository strikt von der installierten Laufzeit.

## Verzeichnisstruktur

Das Git-/Quellrepository kann dauerhaft unverändert unter `/opt/lcs` liegen:

```text
/opt/lcs/
├── install.sh
├── server/
├── system/
└── client/
```

Der Installer schreibt **nie** in dieses Verzeichnis.

Die Standard-Laufzeitziele sind:

```text
/opt/lcs-server/
   server.py, core.py, lcsctl.py
   server.env
   .token
   data/
   releases/
   venv/

/opt/lcs-service/
   bootstrap.py, agent.py, ...
   client.env
   enrollment.token       # nur bis zum erfolgreichen Enrollment
   state/
   features/
   venv/

/opt/lcs-client/
   user_client.py, ...
   venv/
```

Nur die Betriebssystemintegration liegt zwangsläufig außerhalb `/opt`:

```text
/etc/systemd/system/lcs-server.service
/etc/systemd/system/lcs-service.service
/etc/xdg/autostart/lcs-client.desktop
```

## Server installieren

```bash
cd /opt/lcs
./install.sh server
```

Der Server-Enrollment-Token wird bei einer frischen Installation direkt als
`/opt/lcs-server/.token` erzeugt. Das Repository bleibt unangetastet.

Test:

```bash
curl http://127.0.0.1:5000/health
```

Erwartet:

```json
{"ok": true, "version": "0.6"}
```

## Workstation installieren

Auf einem frischen Masterclient muss der Enrollment-Token explizit angegeben werden, z. B. nach sicherem Kopieren der Serverdatei:

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule \
   --token-file /root/lcs-enrollment.token
```

Der Installer kopiert ihn nach `/opt/lcs-service/enrollment.token`. Nach erfolgreichem Enrollment löscht der Agent diese Datei selbst.

Ein bereits enrollter Rechner kann ohne Token aktualisiert werden:

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule
```

## Prüfungsproxy

In `/opt/lcs-service/client.env` kann ergänzt werden:

```ini
LCS_PROXY=http://127.0.0.1:3128
```

## Pfade überschreiben

Die Pfade sind Installer-Defaults, keine im Anwendungsmodell fest verdrahtete Architektur. Beispiele:

```bash
LCS_SERVICE_ROOT=/opt/mein-lcs-service \
LCS_STATE_ROOT=/opt/mein-lcs-service/state \
./install.sh service https://clients.corvi.schule \
   --token-file /root/lcs-enrollment.token
```

Wichtige Variablen sind `LCS_SERVER_ROOT`, `LCS_SERVICE_ROOT`, `LCS_CLIENT_ROOT`, `LCS_STATE_ROOT`, `LCS_FEATURE_ROOT`, `LCS_SERVER_ENV`, `LCS_CLIENT_ENV`, `LCS_SERVER_TOKEN` und `LCS_ENROLLMENT_TOKEN`.

## Migration von v0.4

Der Installer kann bestehende Daten aus `/etc/lcs` und `/var/lib/lcs` übernehmen. Diese alten Orte werden dabei nur gelesen; v0.6 verwendet anschließend die neuen Pfade unter `/opt`.


## Prüfungsimage ohne User-Client

Für Images, auf denen ausschließlich der privilegierte Systemdienst benötigt wird:

```bash
cd /opt/lcs
./install.sh workstation https://clients.example --token-file /pfad/server.token --no-userclient
```

`--no-userclient` installiert/aktualisiert den LCS-Systemdienst, entfernt vorhandene LCS-Autostart- und Menüeinträge und installiert keinen grafischen User-Client.

Bei einer normalen Workstation werden dagegen zwei Desktop-Integrationen erzeugt:

- `/etc/xdg/autostart/lcs-client.desktop` – automatischer Start nach Login
- `/usr/share/applications/lcs-client.desktop` – sichtbarer manueller Starter im Anwendungsmenü
