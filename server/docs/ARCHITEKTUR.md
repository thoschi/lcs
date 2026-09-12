# Architektur – LCS v0.6

LCS trennt Quellcode, Server, privilegierten Systemdienst und Benutzerclient.

```text
/opt/lcs          Git-/Quellrepository, unverändert
/opt/lcs-server   Server-Runtime, Konfiguration, Secret, Daten
/opt/lcs-service  System-Agent, Client-Konfiguration, State, Capability-Cache
/opt/lcs-client   grafischer User-Client
```

Beth/Aleph oder andere Gruppen existieren ausschließlich serverseitig. Jeder Client installiert denselben Systemdienst und denselben User-Client. Der Server berechnet aus Gruppen- und Einzelzuweisungen das effektive Capability-Set.

Der System-Agent synchronisiert Capabilities, führt sämtliche Aktionen aus, hält Heartbeats und puffert Offline-Ergebnisse. Der User-Client spricht nur über einen lokalen Unix-Socket mit ihm. Er zeigt diejenigen System-Capabilities an, die auf dem Server als `user_executable` freigegeben wurden, und fordert deren Ausführung beim Systemdienst an; einen eigenen Serverkontakt besitzt er nicht.

Das Git-Repository ist kein Runtime-State. Der Installer liest daraus Dateien, schreibt jedoch niemals hinein.
