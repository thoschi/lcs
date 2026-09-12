[CmdletBinding()]
param(
   [Parameter(Position = 0)]
   [string]$Mode,
   [Parameter(Position = 1)]
   [string]$ServerUrl,
   [Alias('token-file')]
   [string]$TokenFile,
   [Alias('no-userclient')]
   [switch]$NoUserClient,
   [Parameter(ValueFromRemainingArguments = $true)]
   [string[]]$InstallerArgs
)

$ErrorActionPreference = 'Stop'
$SourceRoot = $PSScriptRoot
$Operation = 'install'
if ($Mode -in @('install', 'upgrade', 'uninstall')) {
   $Operation = $Mode
   $Mode = $ServerUrl
   $ServerUrl = if ($InstallerArgs.Count) { $InstallerArgs[0] } else { '' }
   $InstallerArgs = if ($InstallerArgs.Count -gt 1) { $InstallerArgs[1..($InstallerArgs.Count - 1)] } else { @() }
}
if (($Operation -eq 'uninstall') -and -not $Mode) { $Mode = 'all' }
$TokenSource = if ($TokenFile) { $TokenFile } else { $env:LCS_TOKEN_SOURCE }

$ServerRoot = if ($env:LCS_SERVER_ROOT) { $env:LCS_SERVER_ROOT } else { Join-Path $env:ProgramFiles 'LCS\Server' }
$ServiceRoot = if ($env:LCS_SERVICE_ROOT) { $env:LCS_SERVICE_ROOT } else { Join-Path $env:ProgramFiles 'LCS\Service' }
$ClientRoot = if ($env:LCS_CLIENT_ROOT) { $env:LCS_CLIENT_ROOT } else { Join-Path $env:ProgramFiles 'LCS\Client' }
$DataRoot = if ($env:LCS_STATE_ROOT) { Split-Path $env:LCS_STATE_ROOT -Parent } else { Join-Path $env:ProgramData 'LCS' }
$StateRoot = if ($env:LCS_STATE_ROOT) { $env:LCS_STATE_ROOT } else { Join-Path $DataRoot 'state' }
$FeatureRoot = if ($env:LCS_FEATURE_ROOT) { $env:LCS_FEATURE_ROOT } else { Join-Path $DataRoot 'features' }
$ServerEnv = if ($env:LCS_SERVER_ENV) { $env:LCS_SERVER_ENV } else { Join-Path $ServerRoot 'server.env' }
$ClientEnv = if ($env:LCS_CLIENT_ENV) { $env:LCS_CLIENT_ENV } else { Join-Path $DataRoot 'client.env' }
$EnrollmentToken = if ($env:LCS_ENROLLMENT_TOKEN) { $env:LCS_ENROLLMENT_TOKEN } else { Join-Path $DataRoot 'enrollment.token' }

function Show-Usage {
   Write-Host @"
Aufruf:
  .\install.ps1 server
  .\install.ps1 service https://clients.example --token-file C:\Pfad\token.txt
  .\install.ps1 client https://clients.example
  .\install.ps1 workstation https://clients.example --token-file C:\Pfad\token.txt [--no-userclient]
  .\install.ps1 all https://clients.example
  .\install.ps1 uninstall [server|service|client|workstation|all]
  .\install.ps1 reset-identity

Modi und Optionen entsprechen install.sh. Alle Laufzeitpfade können über
LCS_*_ROOT bzw. LCS_*_ENV überschrieben werden.
"@
}

if (-not $Mode) {
   Show-Usage
   exit 2
}

for ($index = 0; $index -lt $InstallerArgs.Count; $index++) {
   switch ($InstallerArgs[$index]) {
      '--token-file' {
         $index++
         if ($index -ge $InstallerArgs.Count) { throw '--token-file benötigt eine Datei.' }
         $TokenSource = $InstallerArgs[$index]
      }
      '--no-userclient' { $NoUserClient = $true }
      default { Write-Error "Unbekannte Option: $($InstallerArgs[$index])"; Show-Usage; exit 2 }
   }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
   Write-Error 'Bitte in einer PowerShell mit Administratorrechten ausführen.'
   exit 1
}

function Require-ServerUrl {
   if (-not $ServerUrl) { Write-Error "Für $Mode fehlt die Server-URL."; Show-Usage; exit 2 }
}

function Get-Python {
   $python = Get-Command python.exe -ErrorAction SilentlyContinue
   if ($python) { return $python.Source }
   $py = Get-Command py.exe -ErrorAction SilentlyContinue
   if ($py) { return $py.Source }
   throw 'Python 3 wurde nicht gefunden. Bitte Python 3 einschließlich pip und venv installieren.'
}

function New-Venv([string]$Root) {
   $venvPython = Join-Path $Root 'venv\Scripts\python.exe'
   if (-not (Test-Path $venvPython)) {
      $python = Get-Python
      if ([IO.Path]::GetFileName($python) -ieq 'py.exe') { & $python -3 -m venv (Join-Path $Root 'venv') }
      else { & $python -m venv (Join-Path $Root 'venv') }
      if ($LASTEXITCODE) { throw "Python-Umgebung konnte nicht erstellt werden: $Root" }
   }
   return $venvPython
}

function Read-EnvValue([string]$Path, [string]$Key) {
   if (-not (Test-Path $Path)) { return '' }
   $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -Last 1
   if (-not $line) { return '' }
   return ($line.Substring($line.IndexOf('=') + 1)).Trim('"', "'")
}

function Write-Utf8([string]$Path, [string]$Value) {
   $parent = Split-Path $Path -Parent
   if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
   [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($false)))
}

function Protect-File([string]$Path) {
   & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
   if ($LASTEXITCODE) { throw "Dateiberechtigungen konnten nicht gesetzt werden: $Path" }
}

function Clear-Runtime([string]$Root, [string[]]$Keep) {
   if (-not (Test-Path $Root)) { return }
   Get-ChildItem -Force -LiteralPath $Root | Where-Object { $Keep -notcontains $_.Name } | Remove-Item -Recurse -Force
}

function Set-ServerSettings($Settings) {
   $allowed = @('LCS_USER_DATA', 'LCS_REQUIRE_LOCAL_USERNAME')
   $lines = if (Test-Path $ClientEnv) { @(Get-Content -LiteralPath $ClientEnv) } else { @() }
   $lines = @($lines | Where-Object {
      $line = $_
      -not ($allowed | Where-Object { $line.StartsWith($_ + '=') })
   })
   foreach ($key in $allowed) {
      $property = $Settings.PSObject.Properties[$key]
      if ($property -and $property.Value) {
         $value = [string]$property.Value -replace "[`r`n]", ''
         $lines += "$key=$value"
      }
   }
   Write-Utf8 $ClientEnv (($lines -join "`r`n") + "`r`n")
}

function Copy-Tree([string]$Source, [string]$Target) {
   New-Item -ItemType Directory -Force -Path $Target | Out-Null
   Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force
}

function Ensure-EnrollmentToken {
   $deviceState = Join-Path $StateRoot 'device.json'
   if (Test-Path $deviceState) {
      try { $isImageSource = [bool]((Get-Content -Raw -LiteralPath $deviceState | ConvertFrom-Json).image_source) }
      catch { $isImageSource = $false }
      if (-not $isImageSource) { return }
   }
   if ((Test-Path $EnrollmentToken) -and (Get-Item $EnrollmentToken).Length -gt 0) {
      Protect-File $EnrollmentToken
      return
   }
   if ($TokenSource -and (Test-Path $TokenSource) -and (Get-Item $TokenSource).Length -gt 0) {
      New-Item -ItemType Directory -Force -Path (Split-Path $EnrollmentToken -Parent) | Out-Null
      Copy-Item -LiteralPath $TokenSource -Destination $EnrollmentToken -Force
      Protect-File $EnrollmentToken
      return
   }
   if ($ServerUrl) {
      $credential = Get-Credential -UserName $env:COMPUTERNAME -Message 'Passwort für den LCS-Image-Zugang'
      $password = $credential.GetNetworkCredential().Password
      $body = @{ hostname = $env:COMPUTERNAME; password = $password } | ConvertTo-Json
      $response = Invoke-RestMethod -Method Post -Uri ($ServerUrl.TrimEnd('/') + '/api/v1/token/claim') -ContentType 'application/json' -Body $body
      Write-Utf8 $EnrollmentToken ($response.enrollment_token + "`r`n")
      Protect-File $EnrollmentToken
      Set-ServerSettings $response.settings
      return
   }
   throw 'Für den Systemdienst fehlt der Enrollment-Token.'
}

function Write-ClientEnv {
   Require-ServerUrl
   $proxy = Read-EnvValue $ClientEnv 'LCS_PROXY'
   $ca = Read-EnvValue $ClientEnv 'LCS_CA_FILE'
   $userData = Read-EnvValue $ClientEnv 'LCS_USER_DATA'
   $requireLocalUsername = Read-EnvValue $ClientEnv 'LCS_REQUIRE_LOCAL_USERNAME'
   $defaultPassword = Read-EnvValue $ClientEnv 'LCS_DEFAULT_PASSWORD'
   $lines = @(
      "LCS_SERVER=$ServerUrl", 'LCS_HEARTBEAT_SECONDS=20', 'LCS_POLL_SECONDS=10', 'LCS_SYNC_SECONDS=60',
      "LCS_STATE_ROOT=$StateRoot", "LCS_FEATURE_ROOT=$FeatureRoot", "LCS_TOKEN_FILE=$EnrollmentToken", 'LCS_CHANNEL=stable',
      "LCS_DEFAULT_PASSWORD=$(if ($defaultPassword) { $defaultPassword } else { 'corvi' })"
   )
   if ($proxy) { $lines += "LCS_PROXY=$proxy" }
   if ($ca) { $lines += "LCS_CA_FILE=$ca" }
   if ($userData) { $lines += "LCS_USER_DATA=$userData" }
   if ($requireLocalUsername) { $lines += "LCS_REQUIRE_LOCAL_USERNAME=$requireLocalUsername" }
   Write-Utf8 $ClientEnv (($lines -join "`r`n") + "`r`n")
}

function Install-SystemService {
   Require-ServerUrl
   & sc.exe stop LCSService 2>$null | Out-Null
   New-Item -ItemType Directory -Force -Path $ServiceRoot, $StateRoot, $FeatureRoot | Out-Null
   Clear-Runtime $ServiceRoot @('features', 'state', 'venv', 'client.env', 'enrollment.token')
   Copy-Tree (Join-Path $SourceRoot 'system') $ServiceRoot
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ServiceRoot 'linux')
   $python = New-Venv $ServiceRoot
   & $python -m pip install --quiet pywin32
   if ($LASTEXITCODE) { throw 'pywin32 konnte nicht installiert werden.' }
   Write-ClientEnv
   Ensure-EnrollmentToken
   $serviceScript = Join-Path $ServiceRoot 'windows\windows_service.py'
   & $python $serviceScript remove 2>$null | Out-Null
   & $python $serviceScript --startup auto install
   if ($LASTEXITCODE) { throw 'LCS-Systemdienst konnte nicht installiert werden.' }
   & $python $serviceScript start
   Write-Host 'LCS-Systemdienst wurde gestartet.'
   Write-Host "LCS-Systemdienst installiert: $ServiceRoot"
   Write-Host "Konfiguration: $ClientEnv"
   Write-Host "State: $StateRoot"
}

function Install-UserClient {
   Require-ServerUrl
   New-Item -ItemType Directory -Force -Path $ClientRoot | Out-Null
   Clear-Runtime $ClientRoot @('venv')
   Copy-Tree (Join-Path $SourceRoot 'client') $ClientRoot
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ClientRoot 'linux')
   $python = New-Venv $ClientRoot
   & $python -m pip install --quiet -r (Join-Path $ClientRoot 'requirements.txt')
   if ($LASTEXITCODE) { throw 'Abhängigkeiten des LCS-User-Clients konnten nicht installiert werden.' }
   Write-ClientEnv
   $pythonw = Join-Path $ClientRoot 'venv\Scripts\pythonw.exe'
   $script = Join-Path $ClientRoot 'user_client.py'
   $command = '"{0}" "{1}"' -f $pythonw, $script
   $runKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
   New-Item -Path $runKey -Force | Out-Null
   New-ItemProperty -Path $runKey -Name 'LCS User Client' -Value $command -PropertyType String -Force | Out-Null
   $shortcutPath = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\LCS Client.lnk'
   $shell = New-Object -ComObject WScript.Shell
   $shortcut = $shell.CreateShortcut($shortcutPath)
   $shortcut.TargetPath = $pythonw
   $shortcut.Arguments = '"{0}" --show' -f $script
   $shortcut.WorkingDirectory = $ClientRoot
   $shortcut.Save()
   Write-Host "LCS-User-Client installiert: $ClientRoot"
   Write-Host 'Autostart: HKLM\Software\Microsoft\Windows\CurrentVersion\Run'
}

function Remove-UserClientIntegration {
   Remove-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'LCS User Client' -ErrorAction SilentlyContinue
   Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\LCS Client.lnk')
}

function New-RandomHex([int]$Bytes = 32) {
   $buffer = New-Object byte[] $Bytes
   $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
   try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
   return -join ($buffer | ForEach-Object { $_.ToString('x2') })
}

function Install-Server {
   & sc.exe stop LCSServer 2>$null | Out-Null
   New-Item -ItemType Directory -Force -Path $ServerRoot | Out-Null
   Clear-Runtime $ServerRoot @('data', 'releases', 'bootstrap-manifest.json', 'venv', 'server.env')
   foreach ($name in @('core.py', 'server.py', 'lcsctl.py', 'requirements.txt', 'server.env.example')) {
      Copy-Item -LiteralPath (Join-Path $SourceRoot "server\$name") -Destination $ServerRoot -Force
   }
   Copy-Tree (Join-Path $SourceRoot 'server\docs') (Join-Path $ServerRoot 'docs')
   Copy-Tree (Join-Path $SourceRoot 'server\examples') (Join-Path $ServerRoot 'examples')
   Copy-Tree (Join-Path $SourceRoot 'server\web') (Join-Path $ServerRoot 'web')
   Copy-Tree (Join-Path $SourceRoot 'server\windows') (Join-Path $ServerRoot 'windows')
   New-Item -ItemType Directory -Force -Path (Join-Path $ServerRoot 'data'), (Join-Path $ServerRoot 'releases') | Out-Null
   if (-not (Test-Path (Join-Path $ServerRoot 'bootstrap-manifest.json'))) {
      Copy-Item (Join-Path $SourceRoot 'server\bootstrap-manifest.json') $ServerRoot
   }
   $python = New-Venv $ServerRoot
   & $python -m pip install --quiet -r (Join-Path $ServerRoot 'requirements.txt') pywin32
   if ($LASTEXITCODE) { throw 'Server-Abhängigkeiten konnten nicht installiert werden.' }
   $secret = Read-EnvValue $ServerEnv 'LCS_SECRET_KEY'
   if (-not $secret) { $secret = New-RandomHex }
   $values = @{
      LCS_SERVER_HOST = '127.0.0.1'; LCS_SERVER_PORT = '5000'; LCS_OIDC_DISCOVERY_URL = '';
      LCS_OIDC_CLIENT_ID = ''; LCS_OIDC_CLIENT_SECRET = ''; LCS_ADMIN_USERS = ''
   }
   foreach ($key in @($values.Keys)) { $old = Read-EnvValue $ServerEnv $key; if ($old) { $values[$key] = $old } }
   $serverLines = @(
      "LCS_SERVER_DB=$(Join-Path $ServerRoot 'data\lcs.sqlite3')", 'LCS_SESSION_TTL=120', 'LCS_ACTION_LEASE=180',
      'LCS_ACTION_PREFETCH=86400', "LCS_SERVER_HOST=$($values.LCS_SERVER_HOST)", "LCS_SERVER_PORT=$($values.LCS_SERVER_PORT)",
      "LCS_RELEASES_DIR=$(Join-Path $ServerRoot 'releases')", "LCS_SOURCE_ROOT=$SourceRoot", "LCS_MANIFEST_FILE=$(Join-Path $ServerRoot 'bootstrap-manifest.json')",
      "LCS_SECRET_KEY=$secret", "LCS_OIDC_DISCOVERY_URL=$($values.LCS_OIDC_DISCOVERY_URL)",
      "LCS_OIDC_CLIENT_ID=$($values.LCS_OIDC_CLIENT_ID)", "LCS_OIDC_CLIENT_SECRET=$($values.LCS_OIDC_CLIENT_SECRET)",
      "LCS_ADMIN_USERS=$($values.LCS_ADMIN_USERS)", 'LCS_MAX_REQUEST_BYTES=2097152'
   )
   Write-Utf8 $ServerEnv (($serverLines -join "`r`n") + "`r`n")
   Protect-File $ServerEnv
   $serverScript = Join-Path $ServerRoot 'windows\windows_server_service.py'
   & $python $serverScript remove 2>$null | Out-Null
   & $python $serverScript --startup auto install
   if ($LASTEXITCODE) { throw 'LCS-Serverdienst konnte nicht installiert werden.' }
   & $python $serverScript start
   Write-Host "LCS-Server installiert: $ServerRoot"
   Write-Host "Konfiguration: $ServerEnv"
}


function Uninstall-UserClient {
   Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -like "*$ClientRoot*user_client.py*" } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
   Remove-UserClientIntegration
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ClientRoot
   Write-Host 'LCS-User-Client entfernt.'
}

function Uninstall-SystemService {
   & sc.exe stop LCSService 2>$null | Out-Null
   & sc.exe delete LCSService 2>$null | Out-Null
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ServiceRoot
   if (Test-Path $DataRoot) { Remove-Item -Recurse -Force $DataRoot }
   Write-Host 'LCS-Systemdienst und lokaler Zustand entfernt.'
}

function Uninstall-Server {
   & sc.exe stop LCSServer 2>$null | Out-Null
   & sc.exe delete LCSServer 2>$null | Out-Null
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $ServerRoot
   Write-Host 'LCS-Server einschließlich seiner Daten entfernt.'
}

function Uninstall-Product {
   switch ($Mode.ToLowerInvariant()) {
      'client' { Uninstall-UserClient }
      { $_ -in @('service', 'system') } { Uninstall-SystemService }
      'workstation' { Uninstall-UserClient; Uninstall-SystemService }
      'server' { Uninstall-Server }
      'all' { Uninstall-UserClient; Uninstall-SystemService; Uninstall-Server }
      default { Write-Error "Unbekanntes Uninstall-Ziel: $Mode"; Show-Usage; exit 2 }
   }
}

function Reset-Identity {
   & sc.exe stop LCSService 2>$null | Out-Null
   @('device.json', 'device-public.json', 'scheduler.json', 'pending-actions.json', 'result-outbox.json', 'event-outbox.json') |
      ForEach-Object { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $StateRoot $_) }
   Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $FeatureRoot 'packages')
   Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $FeatureRoot 'stack.json')
   Write-Host 'Lokale LCS-Geräteidentität und Capability-Cache wurden gelöscht.'
}

if ($Operation -eq 'uninstall') { Uninstall-Product; exit 0 }

switch ($Mode.ToLowerInvariant()) {
   'server' { Install-Server }
   { $_ -in @('service', 'system') } { Install-SystemService }
   'client' { Install-UserClient }
   'workstation' {
      Install-SystemService
      if ($NoUserClient) { Remove-UserClientIntegration; Write-Host 'LCS-User-Client wurde wegen --no-userclient nicht installiert.' }
      else { Install-UserClient }
   }
   'all' {
      Require-ServerUrl
      Install-Server
      Install-SystemService
      if ($NoUserClient) { Remove-UserClientIntegration; Write-Host 'LCS-User-Client wurde wegen --no-userclient nicht installiert.' }
      else { Install-UserClient }
   }
   'reset-identity' { Reset-Identity }
   default { Show-Usage; exit 2 }
}
