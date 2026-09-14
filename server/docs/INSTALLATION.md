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
Sie startet Gunicorn mit einem regelmäßig erneuerten Worker statt des Flask-Entwicklungsservers.
Dadurch werden Ressourcen spätestens nach der in `LCS_SERVER_MAX_REQUESTS` konfigurierten
Anzahl von Anfragen vollständig freigegeben. Threadzahl, Zeitlimit und Recycling-Grenze
können in `/opt/lcs-server/server.env` angepasst werden.

Für die Webadministration müssen anschließend die `LCS_OIDC_*`-Werte und
`LCS_ADMIN_USERS` in `server.env` gesetzt werden. Danach wird der Dienst mit
`systemctl restart lcs-server` neu gestartet. Für externen Zugriff bleibt ein
TLS-Reverse-Proxy erforderlich, da LCS standardmäßig nur lokal lauscht.

Test:

```bash
curl http://127.0.0.1:5000/health
```

## Frische Workstation / Masterimage

Zuerst in der Webadministration einen vorläufigen Vorlagenzugang mit einem
Passwort und den gewünschten Client-Einstellungen anlegen.
Dabei entsteht zugleich eine dauerhafte Enrollment-Gruppe. Aufgaben, die dort
für neue Mitglieder vorgemerkt werden, erhalten die späteren Klone bereits bei
ihrer ersten Registrierung.
Dazu gehören insbesondere der Benutzerdatenpfad und optional die Bindung an den
lokalen Benutzernamen. Danach:

```bash
cd /opt/lcs
./install.sh install workstation https://clients.corvi.schule
```

Der Installer fragt ausschließlich das Passwort verdeckt ab und lädt den Token
sowie die Einstellungen. Er trägt die Einstellungen automatisch in `client.env`
ein. Der Dienst startet sofort; der Rechner erscheint als Image-Vorlage und
bleibt im Ruhemodus, in dem er ausschließlich seinen Online-Status meldet. Der
User-Client wird weder auf der Vorlage noch auf den Klonen automatisch gestartet.
Auf einem Klon erkennt
der Agent den geänderten Hostnamen, verwirft die kopierte Identität, enrollt den
Rechner separat und richtet das erhaltene Benutzerprofil einschließlich Passwort
und deaktiviertem Autologin ohne Benutzerinteraktion erneut ein. Erst danach sind
dort System- und Benutzerfunktionen aktiv. Nur die Image-Vorlage behält die
Token-Datei.

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

Bei Fehler 1053 kann der Dienstprozess in einer administrativen PowerShell direkt
geprüft werden, ohne den Installer erneut auszuführen:

```powershell
.\install.ps1 diagnose service
```

Die Diagnose prüft zuerst Python, pywin32 und sämtliche Dienstimporte und zeigt
danach die SCM-Registrierung sowie den exakten Befehl für den Vordergrundstart an.
So ist erkennbar, ob bereits Python beziehungsweise ein Import scheitert oder erst
der Start durch den Service Control Manager. Für den anschließend ausgegebenen
Vordergrundstart muss der registrierte Dienst beendet sein, beispielsweise:

```powershell
Stop-Service LCSService -ErrorAction SilentlyContinue
& "$env:ProgramFiles\LCS\Service\venv\Scripts\python.exe" `
  "$env:ProgramFiles\LCS\Service\windows\windows_service.py" debug
```

Damit erscheinen Import- und Startfehler unmittelbar in der Konsole. Beenden lässt
sich der Vordergrundlauf mit `Strg+C`; der normale Dienst wird anschließend mit
`Start-Service LCSService` wieder gestartet. Bei abweichendem `LCS_SERVICE_ROOT`
müssen die beiden Pfade entsprechend angepasst werden.

Bei einer bereits vorhandenen Installation muss nach dem Update erneut
`install.ps1 upgrade service` ausgeführt werden. Dabei werden neben
`pythonservice.exe` auch die zugehörigen pywin32-DLLs in das Stammverzeichnis der
virtuellen Umgebung kopiert. Fehlen sie dort, kann Windows den Diensthost noch vor
dem ersten Python-Import nicht laden und meldet lediglich Fehler 1053.

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

`install.ps1` bietet wie `install.sh` die Operationen `install`, `upgrade` und
`uninstall`, die Modi `server`, `service`/`system`, `client`, `workstation`,
`all` und `reset-identity` sowie die Optionen
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

Ein Proxy ist für die übliche Installation nicht erforderlich. Bei Bedarf wird
er dem Installer als Umgebungsvariable übergeben:

```bash
LCS_PROXY=http://127.0.0.1:3128 \
./install.sh install workstation https://clients.corvi.schule
```

Der Installer nutzt ihn bereits für seine Verbindung zum LCS-Server und trägt
ihn anschließend in `/opt/lcs-service/client.env` ein:

```ini
LCS_PROXY=http://127.0.0.1:3128
```

Ist `LCS_PROXY` beim Upgrade nicht gesetzt, bleibt ein vorhandener Eintrag
erhalten. In PowerShell kann die Variable vor `install.ps1` als
`$env:LCS_PROXY = 'http://127.0.0.1:3128'` gesetzt werden.

## Eigene Pfade

Alle wesentlichen Installationspfade sind überschreibbar, z. B.:

```bash
LCS_SERVICE_ROOT=/opt/custom-service \
LCS_STATE_ROOT=/opt/custom-service/state \
./install.sh service https://clients.corvi.schule \
   --token-file /root/lcs-enrollment.token
```


## Deinstallation

Für einen vollständig sauberen erneuten Test entfernt `uninstall` Dienste,
Autostart-Einträge, Laufzeitdateien, Konfiguration und lokalen Zustand. Ohne Ziel
werden alle LCS-Komponenten entfernt; das Quellrepository `/opt/lcs` bleibt stets
erhalten:

```bash
./install.sh uninstall
./install.sh uninstall workstation
```

Unter Windows stehen dieselben Ziele zur Verfügung:

```powershell
.\install.ps1 uninstall
.\install.ps1 uninstall workstation
```
