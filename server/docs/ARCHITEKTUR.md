# Architektur – LCS v0.7

Der privilegierte Systemdienst besitzt ausschließlich fest einprogrammierte
Fähigkeiten. Er stellt sie über lokalen IPC dem minimalen Nutzerdienst und dem
Benutzermenü bereit und meldet dieselben Metadaten im Heartbeat an den Server.
Der Server speichert und verteilt keine Programme, Skripte oder Capability-Pakete.

Die Erstregistrierung liefert den konfigurierten Benutzerdatenpfad. Credentials
werden lokal gespeichert: unter Linux als vollständige Shadow-Zeile, unter
Windows nur als Benutzerbindung; dort muss das Passwort beim Wiederherstellen
erneut eingegeben werden. Klartextpasswörter werden nie gespeichert.
