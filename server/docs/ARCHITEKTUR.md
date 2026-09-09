# Architektur LCS v0.4

LCS trennt drei Rollen:

- `server/`: Geräteverwaltung, Gruppen, Capability-Zuweisungen, Aktionen und Logging
- `system/`: privilegierter, identischer Systemdienst auf allen Clients
- `client/`: unprivilegierter grafischer Benutzerclient

Beth, Aleph oder andere Gerätegruppen werden ausschließlich serverseitig verwaltet. Ein Client kennt weder seine Gruppe noch ein festes Profil; der Server berechnet bei jedem Manifest-Abruf das effektive Capability-Set.

Der Systemdienst hält eine lokale Kopie des letzten gültigen Capability-Stacks. Ohne Serververbindung arbeitet er mit diesem Cache weiter. Heartbeats, Aktionsresultate und Events werden bei Bedarf lokal gepuffert und später übertragen.

Konkrete Installationspfade werden vom Installer in Environment-Dateien geschrieben. Python-Komponenten verwenden `LCS_*`-Konfigurationen statt Gerätegruppen oder Installationspfade im Programmcode.
