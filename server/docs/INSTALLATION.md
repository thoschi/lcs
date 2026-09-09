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
