# LCS v0.7.2

LCS verwaltet Windows- und Linux-Arbeitsplätze mit einem bewusst kleinen,
lokal funktionsfähigen Client. Der Systemdienst enthält seine Fähigkeiten fest
in der installierten Version. Der Server kann **keinen Code und keine Skripte**
erstellen, verteilen oder nachladen.

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

Bei der ersten Musterclient-Registrierung liefert der Enrollment-Token unter
anderem `LCS_USER_DATA`. `$username` oder `${username}` wird zur Laufzeit durch
den lokalen Sitzungsbenutzer ersetzt. Standardmäßig sind dies unter Linux
`/home/<benutzer>/.config/lcs` und unter Windows das LCS-Verzeichnis in AppData.

`workstation` installiert auf beiden Plattformen den privilegierten
Systemdienst und die Nutzereinrichtung für jede lokale Anmeldung. Unter Linux
erfolgt deren Start über den systemweiten XDG-Autostart, unter Windows über den
systemweiten `Run`-Eintrag. Auf dem Musterclient bleibt die Nutzereinrichtung
gesperrt. Erst nach der Registrierung eines daraus erzeugten Clients wird sie
freigegeben; ein unter Linux vorhandenes Profil wird sofort wiederhergestellt,
andernfalls fragt die Nutzereinrichtung bei der Anmeldung die Zugangsdaten ab.
`--no-userclient` installiert ausdrücklich nur den Systemdienst.

Der System-Marker enthält keine Zugangsdaten. Er ist eine zufällige Kennung,
die nach erfolgreicher Einrichtung sowohl im Systemzustand als auch neben dem
Benutzerprofil gespeichert wird. Stimmen beide Kennungen überein, ist dieses
Profil bereits auf genau diesem Client eingerichtet; fehlt eine davon oder
weicht sie ab, wird die Einrichtung erneut ausgeführt.

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
Änderungen am lokalen Konto aus. Der Nutzerdienst fragt den Status lediglich ab,
weil nur ein Prozess in der angemeldeten Sitzung den Passwortdialog anzeigen
kann. Während ein Musterclient geklont und neu registriert wird, sind diese
Abfragen erwartbar; die DEBUG-Ausgabe nennt, ob Registrierung, Freigabe oder eine
bereits abgeschlossene Einrichtung der Grund für das Warten ist. Die eigentliche
Prüfung wird mit Pfaden und Ergebnis in `C:\ProgramData\LCS\service.log`
protokolliert. Anders als Linux kann Windows keinen kopierten Passwort-Hash aus
dem Profil zurückspielen, weshalb dort bei einer erforderlichen Einrichtung der
Dialog benötigt wird.

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
Beim manuellen Start erscheint das ausführliche DEBUG-Protokoll direkt in der
Konsole. Es wird keine separate Logdatei angelegt.
Beim Upgrade eines bereits aus Musterclient-Daten erzeugten Testclients wird
kein eigener Muster-Token für dessen Hostnamen verlangt. Seine kopierte
Identität bleibt erhalten, bis der Systemdienst den abweichenden Hostnamen
erkennt und das Gerät als normalen Client neu registriert.
