# LCS-Agent für LINBO

Der Agent wird als statische Linux-Binärdatei gebaut. Auf dem LINBO-Client sind
dadurch weder Go noch Python, eine Shell-Installation oder ein Userclient nötig.

## Bauen

Auf einem Rechner mit Go ab Version 1.20 im Wurzelverzeichnis ausführen:

```bash
./build-linbo.sh https://lcs.example
```

`https://lcs.example` ist durch die Adresse des LCS-Servers zu ersetzen. Für
eine andere Clientarchitektur wird `GOARCH` entsprechend gesetzt. Standardmäßig
liegt das Ergebnis unter `build/lcs-linbo-agent`. Ein abweichender Ablageort kann
als zweites Argument angegeben werden:

```bash
GOARCH=arm64 ./build-linbo.sh https://lcs.example /srv/linbo/lcs-linbo-agent
```

Durch die eingebaute Serveradresse benötigt der gestartete Agent nur noch die
Token-Datei. Das Build-Skript bettet außerdem den CA-Vertrauensspeicher des
Build-Rechners ein, da LINBO selbst keinen vollständigen Zertifikatsspeicher
bereitstellt. Für eine eigene Zertifizierungsstelle wird deren PEM-Datei beim
Bauen explizit angegeben:

```bash
LCS_CA_FILE=/pfad/zur/ca-kette.pem ./build-linbo.sh https://lcs.example
```

Das Build-Skript installiert und startet keinen Dienst.

## Manuelle Bereitstellung

Die Binärdatei und eine Token-Datei werden über die vorhandene Dateiverteilung
des LINBO-Servers bei jedem Boot in den Arbeitsspeicher des Clients übertragen.
Die Token-Datei entspricht dem vom LCS-Server ausgegebenen Format; der Token
steht in ihrer ersten Zeile. Da das LINBO-System flüchtig ist, ist weder eine
interaktive Konsole noch ein Imaging-Schritt erforderlich.

Im LCS-Server wird dafür ein **LINBO-/Mehrfach-Token (ohne Musterclient)** und
kein Muster-Token angelegt. Das Feld „Hostname“ bleibt leer. Derselbe Token darf
von mehreren LINBO-Geräten verwendet werden; jedes Gerät wird anhand seines
eigenen Hostnamens als normaler Client im Inventar geführt.

Der serverseitig konfigurierte LINBO-Startbefehl lautet beispielsweise:

```bash
chmod 700 /tmp/lcs-linbo-agent && chmod 600 /tmp/enrollment.token && /tmp/lcs-linbo-agent -token-file /tmp/enrollment.token >>/tmp/lcs-linbo-agent.log 2>&1 &
```

Bei eingebauter Serveradresse kann die Token-Datei auch direkt als einziges
Argument angegeben werden:

```bash
/tmp/lcs-linbo-agent /tmp/enrollment.token
```

Wurde die Serveradresse nicht beim Bauen gesetzt, muss sie beim Start ergänzt
werden:

```bash
/tmp/lcs-linbo-agent -server https://lcs.example -token-file /tmp/enrollment.token >>/tmp/lcs-linbo-agent.log 2>&1 &
```

Für wiederholte LINBO-Starts muss ein LINBO-/Mehrfach-Token verwendet werden.
Ein Einmal-Token ist nach dem ersten Enrollment verbraucht; ein Muster-Token
würde das erste LINBO-Gerät fälschlich als Musterclient einordnen.
