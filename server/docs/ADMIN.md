# Administration – LCS v0.5

CLI:

```bash
LCSCTL="/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py"
```

Beispiele:

```bash
$LCSCTL status
$LCSCTL group-add Beth
$LCSCTL group-add-device Beth beth-042
$LCSCTL capability-publish /opt/lcs-server/examples/capabilities/inventory
$LCSCTL capability-assign inventory group:Beth
$LCSCTL action group:Beth inventory
$LCSCTL device-reset beth-042
```

Server-Konfiguration:

```text
/opt/lcs-server/server.env
```

Enrollment-Token:

```text
/opt/lcs-server/.token
```
