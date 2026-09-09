# LCS v0.4

**Linuxmuster Client/Core/Connection Services**

Ein gemeinsames Paket für Managementserver, privilegierten Systemdienst und grafischen Benutzerclient. Gerätegruppen wie Beth/Aleph existieren ausschließlich auf dem Server; Clients erhalten nur ihr effektives Capability-Set.

## Paketstruktur

```text
lcs/
├── install.sh
├── .token.example
├── server/
├── system/
└── client/
```

Das Paket kann z. B. unter `/opt/lcs` liegen und von dort installiert werden.

## Standardziele

- Server: `/opt/lcs-server`
- Systemdienst: `/opt/lcs-service`
- User-Client: `/opt/lcs-client`
- Konfiguration: `/etc/lcs`
- lokaler Gerätestatus: `/var/lib/lcs`
- Capability-Cache: `/opt/lcs-service/features`

Diese Pfade sind Installer-Defaults und können über `LCS_SERVER_ROOT`, `LCS_SERVICE_ROOT`, `LCS_CLIENT_ROOT`, `LCS_CONFIG_ROOT`, `LCS_STATE_ROOT` und `LCS_FEATURE_ROOT` geändert werden. Der Python-Code liest die tatsächlich verwendeten Pfade aus der Konfiguration.

## Installation

Server:

```bash
cd /opt/lcs
./install.sh server
```

Workstation/Masterimage:

```bash
cd /opt/lcs
./install.sh workstation https://clients.corvi.schule
```

Alternativ einzeln:

```bash
./install.sh service https://clients.corvi.schule
./install.sh client https://clients.corvi.schule
```

Der Systemdienst wird installiert und enabled, auf einem Masterimage aber nicht gestartet.

## Token

Der Bootstrap-/Enrollment-Token liegt im Installationspaket separat als `.token`. Der Server kopiert ihn nach `/etc/lcs/server.token`. Der Systeminstaller kopiert ihn nach `/etc/lcs/enrollment.token`; nach erfolgreichem Enrollment löscht der Agent diese Datei.

Beim Workstation-Install wird die Paketkopie `.token` standardmäßig entfernt, damit sie nicht zusätzlich im Masterimage verbleibt. Mit `LCS_KEEP_PACKAGE_TOKEN=1` kann dieses Verhalten bewusst abgeschaltet werden.

## Verwaltung

```bash
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py status
```

Beispiele:

```bash
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py group-add Beth
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py group-add-device Beth beth-042
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py capability-assign inventory group:Beth
/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py action group:Beth inventory
```

Lokale Identität nur explizit löschen:

```bash
./install.sh reset-identity
```

Normale Installationen/Updates erhalten die vorhandene Geräteidentität.
