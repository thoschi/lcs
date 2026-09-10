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

Zuerst in der Webadministration einen Image-Zugang mit dem Hostnamen des
Masterrechners und einem Passwort anlegen. Danach:

```bash
cd /opt/lcs
./install.sh install workstation https://clients.corvi.schule
```

Der Installer fragt das Passwort verdeckt ab und lädt den Token. Der Dienst
startet sofort; der Rechner erscheint als Image-Vorlage. Auf einem Klon erkennt
der Agent den geänderten Hostnamen, verwirft die kopierte Identität und enrollt
den Rechner separat. Nur die Image-Vorlage behält die Token-Datei.

## Client bleibt in der Verwaltung offline

Die Registrierung allein belegt nur ein erfolgreiches Enrollment. Als online gilt
ein Client erst, wenn sein Systemdienst innerhalb der letzten 60 Sekunden einen
Heartbeat gesendet hat. Der Dienst wird bereits vom Installer gestartet. Zur Diagnose:

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
.\install.ps1 install workstation https://clients.corvi.schule
```

`install.ps1` bietet wie `install.sh` die Modi `server`, `service`/`system`,
`client`, `workstation`, `all` und `reset-identity` sowie die Optionen
`--token-file` und `--no-userclient`. Die Standardpfade sind
`%ProgramFiles%\LCS\Server`, `%ProgramFiles%\LCS\Service`,
`%ProgramFiles%\LCS\Client` und `%ProgramData%\LCS`.

## Update eines bereits enrollten Clients

```bash
cd /opt/lcs
./install.sh upgrade workstation https://clients.corvi.schule
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
