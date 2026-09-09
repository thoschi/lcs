# Installation – LCS v0.5

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

Der Systemdienst ist danach aktiviert, wird vom Installer aber bewusst nicht gestartet. Erst ein echter Client soll sich enrollen.

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
