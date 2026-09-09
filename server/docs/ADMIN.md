# Administration – LCS v0.6

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
$LCSCTL token-create "Beth-Image 2026"
$LCSCTL tokens
$LCSCTL token-revoke "Beth-Image 2026"
```

## Webadministration und Keycloak

Die Weboberfläche unter `/admin` zeigt Clients und Gruppen, bearbeitet
Capability-Metadaten und -Zuweisungen, plant Aktionen ein und verwaltet
Enrollment-Tokens. Sie verwendet OpenID Connect über Authlib. In `server.env`
werden mindestens diese Werte gesetzt:

```ini
LCS_SECRET_KEY=<zufälliger dauerhafter Wert>
LCS_OIDC_DISCOVERY_URL=https://keycloak.example/realms/schule/.well-known/openid-configuration
LCS_OIDC_CLIENT_ID=lcs
LCS_OIDC_CLIENT_SECRET=<Keycloak-Client-Secret>
LCS_ADMIN_USERS=administrator,admin@example.org
```

Die vorläufige Berechtigungsprüfung vergleicht `preferred_username`, ersatzweise
E-Mail oder Subject, exakt mit der kommaseparierten Admin-Liste. Als gültige
Redirect-URI wird in Keycloak `https://<LCS-Host>/auth/callback` eingetragen.

Der LCS-Prozess lauscht standardmäßig nur auf `127.0.0.1:5000`. Ein Reverse
Proxy muss TLS terminieren, Host und Protokoll weiterreichen und `/`, `/admin`,
`/auth` sowie `/api` an LCS weiterleiten.

## Enrollment-Tokens

Tokens sind benannt und für mehrere Geräte eines Images wiederverwendbar. In der
Weboberfläche kann ein Token jederzeit widerrufen oder erneut aktiviert werden.
Der Klartext wird nicht gespeichert und nur beim Erzeugen angezeigt. Der bei
der Erstinstallation erzeugte Token wird als `Legacy-Token` importiert und kann
danach genauso widerrufen werden.

## Clients generalisieren und löschen

**Generalisieren** plant einen Reset als Systemaktion ein. Der Client bestätigt
den Auftrag, entfernt danach Geräteidentität, Enrollment-Token, Scheduler-State
und Capability-Cache und stoppt seinen Systemdienst. Mit der Bestätigung löscht
der Server gleichzeitig Sessions, Aktionen, Ereignisse, Gruppen- und
Capability-Zuordnungen sowie den Geräte-Datensatz. Bei einem Verbindungsabbruch
wird die Bestätigung erneut versucht; eine anschließende `401` gilt als
Bestätigung, dass der Server den Client bereits gelöscht hat.

**Sofort löschen** entfernt ausschließlich die Serverdaten. Diese Variante ist
für dauerhaft verlorene oder bereits anderweitig generalisierte Geräte gedacht.
Ein noch aktiver Client wird dadurch nicht lokal bereinigt.

## Aktionsfeedback

Systemaktionen wechseln von `queued` über `running` nach `done` oder `failed`.
Der Client meldet Exit-Code, Ergebnis, Standardfehler oder eine Fehlermeldung
zurück. Abschlusszeit und vollständige Rückmeldung sind in der Aktionstabelle
der Webadministration aufklappbar. Nicht übertragene Ergebnisse werden lokal
gepuffert und später erneut gesendet.

Server-Konfiguration:

```text
/opt/lcs-server/server.env
```

Enrollment-Token:

```text
/opt/lcs-server/.token
```
