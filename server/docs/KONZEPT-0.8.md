# LCS 0.8 – geprüftes Dienstkonzept

## Einschätzung

Die Trennung in **Moderator**, **System-Executor** und **Benutzer-Executor** ist
gegenüber dem bisherigen, teilweise synchronen Agenten die richtige Richtung.
Der Moderator besitzt Netzwerk und Zustand, die Executor-Schicht führt nur
lokal bekannte, allowlist-basierte Aktionen aus. Lange Aktionen blockieren den
Heartbeat deshalb nicht. Registrierung und Ersteinrichtung bleiben voneinander
unabhängig; insbesondere darf fehlendes Netzwerk die Personalisierung nicht
verhindern.

Folgende Präzisierungen vermeiden logische und sicherheitsrelevante Fehler:

* Ein Server darf in 0.8 **keinen beliebigen Code** schicken. Er übermittelt nur
  eine Capability-ID und validierte Parameter. Skriptverteilung ist eine spätere,
  separat abzusichernde Protokollerweiterung (Signatur, Freigabe, Audit, Timeout).
* Klartextpasswörter existieren nur im Prozessspeicher des Anmeldedialogs und der
  unmittelbaren IPC-Anfrage. Sie werden weder protokolliert noch gespeichert.
  Der Linux-Shadow-Auszug ist ebenfalls ein Geheimnis und wird mit Modus 0600
  gespeichert. Windows kann keinen wiederverwendbaren Passwort-Hash exportieren
  und fragt deshalb bei einer Wiederherstellung erneut.
* Offline bedeutet: Enrollment-Information, Modus und Benutzerdatenpfad müssen
  bereits im Image liegen. Es bedeutet nicht, dass Schulnetz-Zugangsdaten offline
  geprüft werden können. LCS übernimmt sie in diesem Fall als vom Nutzer
  gelieferte Identität.
* Der Musterclient behält den Enrollment-Token, registriert aber keine geklonte
  Geräteidentität. Ein abweichender Hostname verwirft die kopierte Identität und
  löst auf dem Klon Enrollment aus.
* `reset` fragt vor dem Löschen der Identität den Server nach dem Enrollment-Token
  des lokal gespeicherten Musterclient-Hostnamens. Dieser Hostname stammt aus der
  Token-Datei und bleibt im Gerätezustand erhalten. Musterclients selbst werden
  nicht auf diesem Weg zurückgesetzt. Ist der Server nicht erreichbar, muss der
  Reset abbrechen, statt einen nicht mehr registrierbaren Client zu erzeugen.

## Betriebsmodi

| Modus | Nutzername | Passwortabfrage | Profil |
|---|---|---|---|
| Domäne | angemeldete Sitzung | nur wenn eine Einrichtungsaktion Klartext braucht | pro Domänennutzer |
| Standalone | Schulnetzname aus Dialog/Profil | bei Erstanlage; unter Windows auch Wiederherstellung | LCS-Verzeichnis, Linux zusätzlich Shadow-Zeile |
| Nutzerlos | keiner | keine | keines; nur Systemdienst |

Nach Standalone-Personalisierung werden Autologin und Default-Zugang entfernt und
die Sitzung beendet. Im Domänenmodus werden lokales Konto und Autologin nicht
verändert.

## Komponenten und Protokoll

1. **LCS-Moderator**: lokale Ersteinrichtung, Enrollment, Heartbeat, persistente
   Empfangs-/Ergebnis-Outbox und Weitergabe an Executor.
2. **System-Executor**: eine FIFO-Queue in einem eigenen Worker-Thread. Aktionen
   laufen privilegiert, während Moderator und IPC antwortfähig bleiben.
3. **Benutzerdienst**: läuft je Sitzung und ist die Vertrauensgrenze für spätere
   Aktionen im echten Benutzerkontext. System-IPC ist Unix-Socket bzw. Windows
   Named Pipe; niemals eine gemeinsam beschreibbare temporäre Datei.
4. **Server-App**: Enrollment, Inventar, Gruppen, Capability-Zuweisung, Queues,
   Ergebnisse und Audit in SQLite; bestehende Web-Oberfläche bleibt erhalten.
5. **LINBO-Dienst**: derselbe Enrollment-/Heartbeat-/Aktionsvertrag, ergänzt um
   fest eingebaute `linbo_cmd`-Capabilities.
6. **LINBO-Server-Dienst**: kleiner, token-authentifizierter HTTP-Dienst auf
   Loopback. Er akzeptiert ausschließlich fest definierte Verwaltungsaktionen.

Die lokale Queue ist absichtlich speicherbasiert; Serveraktionen werden vor der
Ausführung zusätzlich in `pending-actions.json` persistiert. So bleibt die
Implementierung klein, übersteht aber Verbindungsabbrüche und Neustarts.

## Noch offene Grenzen

Die Weitergabe serverseitiger Aktionen in eine konkrete grafische Sitzung braucht
vor einer Skriptfunktion noch eine Sitzungsbindung (Benutzer, Login-ID, Ablaufzeit)
und eine persistente Benutzer-Queue. Ebenso sollten TLS/mTLS, Tokenrotation,
Maximalgrößen, Aktionsabbruch und Aufbewahrungsfristen vor produktivem Einsatz
verbindlich konfiguriert werden. Diese Punkte sind bewusst nicht durch einen
allgemeinen Remote-Shell-Mechanismus vorweggenommen.
