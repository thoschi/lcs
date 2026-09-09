# Architektur – LCS v0.6

LCS trennt Quellcode, Server, privilegierten Systemdienst und Benutzerclient.

```text
/opt/lcs          Git-/Quellrepository, unverändert
/opt/lcs-server   Server-Runtime, Konfiguration, Secret, Daten
/opt/lcs-service  System-Agent, Client-Konfiguration, State, Capability-Cache
/opt/lcs-client   grafischer User-Client
```

Beth/Aleph oder andere Gruppen existieren ausschließlich serverseitig. Jeder Client installiert denselben Systemdienst und denselben User-Client. Der Server berechnet aus Gruppen- und Einzelzuweisungen das effektive Capability-Set.

Der System-Agent synchronisiert Capabilities, führt Systemaktionen aus, hält Heartbeats und puffert Offline-Ergebnisse. Der User-Client liest denselben lokalen Capability-Stack und zeigt ausschließlich `user`-Capabilities an.

Das Git-Repository ist kein Runtime-State. Der Installer liest daraus Dateien, schreibt jedoch niemals hinein.
