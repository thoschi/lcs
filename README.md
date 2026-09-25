# LCS v0.8.0

LCS verwaltet Windows- und Linux-Arbeitsplätze mit einem bewusst kleinen,
lokal funktionsfähigen Client. Der Systemdienst enthält seine Fähigkeiten fest
in der installierten Version. Der Server kann **keinen Code und keine Skripte**
erstellen, verteilen oder nachladen.

Die geprüfte Architektur, Betriebsmodi, Sicherheitsgrenzen und noch offenen
Punkte sind in [`server/docs/KONZEPT-0.8.md`](server/docs/KONZEPT-0.8.md)
dokumentiert. Systemaktionen laufen seit 0.8 über einen eigenen FIFO-Executor,
damit Netzwerk, IPC und Heartbeats auch während einer Aktion antwortfähig bleiben.

## Komponenten

* **`lcs-service`** läuft privilegiert, ermittelt Basisinformationen, setzt das
  lokale Passwort und führt die eingebauten Funktionen Herunterfahren, Neustart
  und Abmelden aus. Diese Funktionen arbeiten auch ohne Serververbindung.
* **`lcs-userservice`** startet bei der Benutzeranmeldung ausschließlich für die
  einmalige Abfrage von Schulnetz-Login und -Passwort. Unter Linux stellt er eine
  bereits gespeicherte Shadow-Zeile ohne Dialog wieder her. Nach erfolgreicher
  Einrichtung eines lokalen Kontos wird Autologin deaktiviert und die Sitzung
  beendet. Bei Domänenanmeldung wird dagegen nur der benutzerspezifische
  LCS-Speicher eingerichtet und einmalig das Schulnetz-Passwort abgefragt.
* **LCS Benutzeraktionen** ist ein separat aufrufbares Menü. Es zeigt genau die
  vom lokalen Systemdienst als benutzerausführbar gemeldeten Fähigkeiten.
* **`lcs-server`** registriert Geräte, zeigt deren Basisinformationen und die vom
  jeweiligen Client gemeldeten Fähigkeiten und kann nur diese Funktionen
  anfordern.

## Basisinformationen

Der Dienst meldet seine Version und Fähigkeiten sowie Hostname, MAC- und
IP-Adressen, Betriebssystem und Version, Architektur, Seriennummer, angemeldete
Benutzer und den Prüfungsmodus. Als Prüfungsmodus gilt ein laufender Squid-Dienst.
Die Ermittlung ist lokal, zeitlich begrenzt und benötigt den Server nicht.

## Installation

Linux (als root):

```bash
./install.sh install workstation https://lcs.example
```

Windows (administrative PowerShell):

```powershell
.\install.ps1 install workstation https://lcs.example
```

LINBO-Client und der optionale Dienst auf dem LINBO-Server werden unter Linux
separat installiert:

```bash
./install.sh install linbo https://lcs.example --token-file ./enrollment.token
./install.sh install linbo-server
```

`upgrade` ersetzt dabei nur Programmcode; State, Enrollment-Token und `.env`
bleiben erhalten. Eine erneute `install` entfernt dagegen vorhandene Reste der
jeweiligen Komponente. `reset-identity` fordert zuerst authentifiziert genau den
Enrollment-Token des lokal gespeicherten Musterclient-Hostnamens an und bricht
bei Nichterreichbarkeit sicher ab. Heruntergeladene Token-Dateien enthalten
dafür in der zweiten Zeile den Hostnamen; ältere einzeilige Dateien bleiben
lesbar.

`LCS_USER_DATA` kann in der lokalen Client-Konfiguration gesetzt werden.
`$username` oder `${username}` wird zur Laufzeit durch den lokalen
Sitzungsbenutzer ersetzt. Standardmäßig sind dies unter Linux
`/home/<benutzer>/.config/lcs` und unter Windows das LCS-Verzeichnis in AppData.

`workstation` installiert auf beiden Plattformen den privilegierten
Systemdienst und die Nutzereinrichtung für jede lokale Anmeldung. Unter Linux
erfolgt deren Start über den systemweiten XDG-Autostart, unter Windows über den
systemweiten `Run`-Eintrag. Die Registrierung ermittelt zuerst, ob es sich um
einen Musterclient handelt. Nur auf normalen Clients wird danach die
Nutzereinrichtung ausgeführt. Ein unter Linux vorhandenes Profil wird dabei
lokal wiederhergestellt, andernfalls fragt die Nutzereinrichtung bei der
Anmeldung die Zugangsdaten ab.
`--no-userclient` installiert ausdrücklich nur den Systemdienst.

Der System-Marker enthält keine Zugangsdaten. Er ist eine zufällige Kennung,
die nach erfolgreicher Einrichtung sowohl im Systemzustand als auch neben dem
Benutzerprofil gespeichert wird. Stimmen beide Kennungen überein und enthält
das Profil die plattformspezifischen Kontodaten, ist es auf genau diesem Client
eingerichtet. Fehlt eine Kennung, weicht sie ab oder fehlt unter Linux die
Shadow-Zeile, wird die Einrichtung erneut ausgeführt.

Ist „Angemeldeten Domänenbenutzer übernehmen“ aktiviert, gilt die Einrichtung
für jeden Domänenbenutzer getrennt. Sein Benutzername ist bereits bekannt; der
Dialog fragt daher nur das Schulnetz-Passwort ab. LCS legt den konfigurierten
Benutzerdatenpfad an, speichert das Passwort aber nicht. Lokales Passwort,
Shadow-Zeile und Autologin-Konfiguration bleiben in diesem Modus unverändert.

Server, Systemdienst und Benutzerprogramme werden mit `install server`,
`install service` beziehungsweise `install client` einzeln installiert. Ein
Upgrade erfolgt entsprechend mit `upgrade` statt `install`.
Unter Windows startet die Nutzereinrichtung über den systemweiten `Run`-Eintrag
bei jeder Benutzeranmeldung im richtigen Benutzerkontext. Sie wartet dauerhaft
auf Anforderungen des Systemdienstes und bleibt auch nach einer abgeschlossenen
Interaktion aktiv. Nach Installation oder Upgrade ist eine neue Anmeldung
erforderlich.

Der privilegierte Systemdienst prüft Profil und System-Marker und führt alle
Änderungen am lokalen Konto aus. Diese lokale Einrichtung wird immer vor der
Registrierung abgeschlossen und benötigt keine Serververbindung. Fehlt das
Profil, fragt der Nutzerdienst Schulnetz-Login und Passwort ab. Ist auf einem
Autologin-Rechner bereits ein Profil vorhanden, stellt Linux dessen Shadow-Zeile
wieder her; Windows fragt das Passwort erneut ab. Anschließend werden Autologin
deaktiviert und die Sitzung beendet. Stimmen Profil- und System-Marker bereits
überein, fährt der Dienst ohne Benutzerinteraktion mit der Registrierung fort.
Windows und Linux verwenden gemeinsam `credentials.json`. LCS übernimmt daraus
den Schulnetz-Login und zeigt ihn im Dialog ausgegraut an. Fehlt die lokale
Passworteinrichtung, wird das Passwort erneut abgefragt und nicht gespeichert.

Zum Prüfen und zum manuellen Testen in der angemeldeten Benutzersitzung:

```powershell
Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' `
   -Name 'LCS User Service'
Get-CimInstance Win32_Process | Where-Object CommandLine -Like '*user_service.py*' |
   Select-Object ProcessId, SessionId, CommandLine
# Nur ausführen, falls kein Prozess angezeigt wird:
& "$env:ProgramFiles\LCS\Client\venv\Scripts\python.exe" `
   "$env:ProgramFiles\LCS\Client\user_service.py"
```
Beim Upgrade eines bereits aus Musterclient-Daten erzeugten Testclients wird
kein eigener Muster-Token für dessen Hostnamen verlangt. Seine kopierte
Identität bleibt erhalten, bis der Systemdienst den abweichenden Hostnamen
erkennt und das Gerät als normalen Client neu registriert.
