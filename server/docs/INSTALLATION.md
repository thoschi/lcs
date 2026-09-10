# Installation – LCS v0.6

## Quellrepository

Das Repository kann dauerhaft unter `/opt/lcs` liegen und wird vom Installer nicht verändert:

```bash
cd /opt/lcs
```

## Server

```bash
./install.sh server
```

Standardmäßig entstehen ausschließlich LCS-Laufzeitdaten unter `/opt/lcs-server`:

```text
/opt/lcs-server/server.env
/opt/lcs-server/.token
/opt/lcs-server/data/
/opt/lcs-server/releases/
/opt/lcs-server/venv/
```

Die systemd-Unit liegt systembedingt unter `/etc/systemd/system/lcs-server.service`.

Für die Webadministration müssen anschließend die `LCS_OIDC_*`-Werte und
`LCS_ADMIN_USERS` in `server.env` gesetzt werden. Danach wird der Dienst mit
`systemctl restart lcs-server` neu gestartet. Für externen Zugriff bleibt ein
TLS-Reverse-Proxy erforderlich, da LCS standardmäßig nur lokal lauscht.

Test:

```bash
curl http://127.0.0.1:5000/health
```

## Frische Workstation / Masterimage

Den Enrollment-Token sicher vom Server auf den Master übertragen, z. B. nach `/root/lcs-enrollment.token`, und dann:

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule \
   --token-file /root/lcs-enrollment.token
```

Installiert werden:

```text
/opt/lcs-service/
/opt/lcs-client/
```

Bei einem frischen Image wird der Systemdienst aktiviert, aber bewusst nicht
gestartet. Erst ein echter Client soll sich enrollen. Erkennt der Installer
hingegen eine vorhandene Geräteidentität, startet er den Dienst nach dem Update
sofort wieder.

Nach erfolgreichem Enrollment löscht der Agent `/opt/lcs-service/enrollment.token`.

## Client bleibt in der Verwaltung offline

Die Registrierung allein belegt nur ein erfolgreiches Enrollment. Als online gilt
ein Client erst, wenn sein Systemdienst innerhalb der letzten 60 Sekunden einen
Heartbeat gesendet hat. Bei einem frischen Linux-Image ist der Dienst absichtlich
nur aktiviert und läuft erst nach dem nächsten Start. Für einen Test ohne Neustart:

```bash
systemctl start lcs-service.service
systemctl status lcs-service.service
journalctl -u lcs-service.service -n 50 --no-pager
```

Unter Windows müssen Status und letzte Meldungen des Dienstes entsprechend in
einer administrativen PowerShell geprüft werden:

```powershell
Get-Service LCSService
Get-Content "$env:ProgramData\LCS\service.log" -Tail 50
```

Der Windows-Dienst schreibt seine Agent-Ausgaben in diese Logdatei. Im
Windows-Ereignisprotokoll stehen dagegen nur Start und unerwartetes Dienstende.

Wiederholte Meldungen `heartbeat unavailable` sprechen für URL-, TLS-, Proxy- oder
Netzwerkprobleme. `heartbeat failed` mit HTTP 401 weist dagegen auf eine nicht mehr
gültige lokale Geräteidentität hin.

### Windows-Workstation

Python 3 muss installiert sein. Danach in einer PowerShell mit
Administratorrechten:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\Pfad\zu\lcs
.\install.ps1 workstation https://clients.corvi.schule `
   --token-file C:\Pfad\lcs-enrollment.token
```

`install.ps1` bietet wie `install.sh` die Modi `server`, `service`/`system`,
`client`, `workstation`, `all` und `reset-identity` sowie die Optionen
`--token-file` und `--no-userclient`. Die Standardpfade sind
`%ProgramFiles%\LCS\Server`, `%ProgramFiles%\LCS\Service`,
`%ProgramFiles%\LCS\Client` und `%ProgramData%\LCS`.

## Update eines bereits enrollten Clients

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule
```

Ein neuer Bootstrap-Token ist nicht nötig, solange `/opt/lcs-service/state/device.json` vorhanden ist.

## Prüfungsproxy

In `/opt/lcs-service/client.env`:

```ini
LCS_PROXY=http://127.0.0.1:3128
```

## Eigene Pfade

Alle wesentlichen Installationspfade sind überschreibbar, z. B.:

```bash
LCS_SERVICE_ROOT=/opt/custom-service \
LCS_STATE_ROOT=/opt/custom-service/state \
./install.sh service https://clients.corvi.schule \
   --token-file /root/lcs-enrollment.token
```
