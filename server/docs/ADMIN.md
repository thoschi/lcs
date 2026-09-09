# LCS Admin-Kurzreferenz

```bash
LCSCTL="/opt/lcs-server/venv/bin/python /opt/lcs-server/lcsctl.py"
$LCSCTL status
$LCSCTL groups
$LCSCTL group-add Beth
$LCSCTL group-add-device Beth beth-042
$LCSCTL capability-publish /opt/lcs-server/examples/capabilities/inventory
$LCSCTL capability-assign inventory group:Beth
$LCSCTL action group:Beth inventory
$LCSCTL logs
```

Serverdienst:

```bash
systemctl status lcs-server
journalctl -u lcs-server
```

Client-Systemdienst:

```bash
systemctl status lcs-service
journalctl -u lcs-service
```
