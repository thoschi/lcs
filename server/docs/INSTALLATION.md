# Installation LCS v0.4

## Server

Das Paket liegt beispielsweise unter `/opt/lcs`:

```bash
cd /opt/lcs
sudo ./install.sh server
```

Standardmäßig entstehen `/opt/lcs-server`, `/etc/lcs/server.env` und `/etc/lcs/server.token`. Bestehende v0.2/v0.3-Daten aus den alten LMN-Pfaden werden übernommen, sofern am neuen Ziel noch keine Datenbank vorhanden ist.

Test:

```bash
curl http://127.0.0.1:5000/health
```

Erwartet wird Version `0.4`.

## Masterimage / Workstation

Falls `.token` nicht bereits im Paket liegt, vom Managementserver holen:

```bash
scp root@clients:/etc/lcs/server.token /opt/lcs/.token
chmod 600 /opt/lcs/.token
```

Dann:

```bash
cd /opt/lcs
sudo ./install.sh workstation https://clients.corvi.schule
```

Der Dienst `lcs-service.service` wird enabled, aber nicht gestartet. Dadurch landet keine Geräteidentität im Masterimage.

Für den Prüfungsbetrieb kann `/etc/lcs/client.env` z. B. ergänzt werden um:

```ini
LCS_PROXY=http://127.0.0.1:3128
```

Prüfung vor dem Imaging:

```bash
systemctl is-enabled lcs-service
systemctl is-active lcs-service
ls -l /var/lib/lcs/device.json
```

Erwartet: `enabled`, nicht aktiv, keine `device.json`.

## Andere Installationspfade

Beispiel:

```bash
LCS_SERVICE_ROOT=/srv/lcs-service \
LCS_STATE_ROOT=/srv/lcs-state \
./install.sh service https://clients.corvi.schule
```

Der Installer schreibt diese Werte in die Konfiguration und rendert die Service-Dateien passend dazu.
