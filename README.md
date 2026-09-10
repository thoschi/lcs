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

Nach der Installation werden Keycloak und die vorläufige Admin-Liste in
`/opt/lcs-server/server.env` konfiguriert:

```ini
LCS_OIDC_DISCOVERY_URL=https://keycloak.example/realms/schule/.well-known/openid-configuration
LCS_OIDC_CLIENT_ID=lcs
LCS_OIDC_CLIENT_SECRET=client-secret
LCS_ADMIN_USERS=administrator,admin@example.org
```

Die Redirect-URI des Keycloak-Clients lautet
`https://clients.example/auth/callback`. Die Administration ist anschließend
unter `https://clients.example/admin` erreichbar. Der lokale Server bleibt
bewusst an `127.0.0.1` gebunden und benötigt für entfernte Clients einen
TLS-terminierenden Reverse Proxy.

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

### Windows

In einer PowerShell mit Administratorrechten stehen dieselben Modi und Optionen
wie unter Linux zur Verfügung:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\Pfad\zu\lcs
.\install.ps1 workstation https://clients.corvi.schule `
   --token-file C:\Pfad\lcs-enrollment.token
```

Standardmäßig werden der Systemdienst unter `%ProgramFiles%\LCS\Service`, der
User-Client unter `%ProgramFiles%\LCS\Client` und Konfiguration sowie State unter
`%ProgramData%\LCS` installiert. Der Systemdienst läuft als `LCSService`; der
User-Client wird für alle Benutzer beim Login gestartet. Python 3 muss vorhanden
sein, weitere benötigte Python-Pakete installiert das Skript in lokale venvs.

## Enrollment-Tokens pro Image

Die Webadministration kann mehrere benannte, wiederverwendbare Tokens erzeugen.
Der vollständige Wert wird nur einmal direkt nach dem Erzeugen angezeigt. So
kann jedes Masterimage einen eigenen Token erhalten. Wird ein Image ausgemustert
oder ein Token kompromittiert, lässt sich nur dieser Token widerrufen; bereits
enrollte Clients behalten ihre Geräteidentität.

Alternativ steht die lokale Server-CLI zur Verfügung:

```bash
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py token-create "Image 2026-09"
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py tokens
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py token-revoke "Image 2026-09"
```

Ein bereits enrollter Rechner kann ohne Token aktualisiert werden:

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule
```

In der Clientübersicht der Webadministration kann ein Gerät generalisiert
werden. Dabei erzeugt der Server einen einmaligen, 30 Tage gültigen Token und liefert
ihn mit dem Rücksetzbefehl aus. Der Server wartet auf die Bestätigung des Clients;
anschließend werden die lokale Identität und der Capability-Cache sowie sämtliche
zugehörigen Serverdaten entfernt. Beim nächsten Dienststart registriert sich der Client
mit dem neuen Token selbstständig wieder. Für nicht mehr erreichbare Geräte gibt es
zusätzlich eine ausdrücklich serverseitige Sofortlöschung. Ergebnisse und
Fehler normaler Systemaktionen werden in der Aktionstabelle angezeigt.

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

`LCS_USER_DATA` legt optional den persönlichen Datenspeicher des User-Clients
fest (Standard: `~/.config/lcs/data`, unter Windows `%APPDATA%\LCS\data`). Beim
Start werden darin `backgrounds`, `printers` und `state` angelegt. User-Capabilities
erhalten den Pfad als `context["data_path"]`.

Capabilities können `startup`-, `interval`- oder tägliche `daily`-Trigger besitzen. Ohne Trigger
sind sie manuell bzw. als einmalige Serveraktion nutzbar; abgearbeitete Aktionen
werden aus der Queue gelöscht, ihr Ergebnis bleibt im Ereignisprotokoll. Bei
`"requires_password": true` fragt der User-Client das Passwort mit dem Text aus
`password_reason` genau einmal je Sitzung ab. Die Bedingung
`{"type": "password_unset"}` führt eine Aktion nur aus, wenn `passwd -S` sicher
den Zustand `NP` (kein Passwort) meldet. Auch ein gesperrtes Passwort (`L`) gilt
als vorhanden. Bei unbekanntem Zustand wird die Aktion bewusst
übersprungen; es wird kein Testpasswort ausprobiert oder gespeichert.

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
