#!/bin/bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
OPERATION="install"
MODE="${1:-}"
if [ "$MODE" = "install" ] || [ "$MODE" = "upgrade" ]; then
   OPERATION="$MODE"
   shift || true
   MODE="${1:-}"
fi
shift || true

SERVER_URL=""
TOKEN_SOURCE="${LCS_TOKEN_SOURCE:-}"

LCS_SERVER_ROOT="${LCS_SERVER_ROOT:-/opt/lcs-server}"
LCS_CLIENT_ROOT="${LCS_CLIENT_ROOT:-/opt/lcs-client}"
LCS_SERVICE_ROOT="${LCS_SERVICE_ROOT:-/opt/lcs-service}"
LCS_STATE_ROOT="${LCS_STATE_ROOT:-$LCS_SERVICE_ROOT/state}"
LCS_FEATURE_ROOT="${LCS_FEATURE_ROOT:-$LCS_SERVICE_ROOT/features}"
LCS_SERVER_USER="${LCS_SERVER_USER:-lcs}"
LCS_SYSTEMD_ROOT="${LCS_SYSTEMD_ROOT:-/etc/systemd/system}"
LCS_AUTOSTART_ROOT="${LCS_AUTOSTART_ROOT:-/etc/xdg/autostart}"
LCS_APPLICATIONS_ROOT="${LCS_APPLICATIONS_ROOT:-/usr/share/applications}"
NO_USERCLIENT=0
LCS_SERVER_ENV="${LCS_SERVER_ENV:-$LCS_SERVER_ROOT/server.env}"
LCS_CLIENT_ENV="${LCS_CLIENT_ENV:-$LCS_SERVICE_ROOT/client.env}"
LCS_ENROLLMENT_TOKEN="${LCS_ENROLLMENT_TOKEN:-$LCS_SERVICE_ROOT/enrollment.token}"

if [ "$(id -u)" -ne 0 ]; then
   echo "Bitte als root ausführen." >&2
   exit 1
fi

usage() {
   cat <<EOF2
Aufruf:
  $0 install server
  $0 upgrade server
  $0 service https://clients.example --token-file /pfad/zur/token-datei
  $0 client https://clients.example
  $0 install workstation https://clients.example [--no-userclient]
  $0 upgrade workstation https://clients.example [--no-userclient]
  $0 all https://clients.example
  $0 reset-identity

install        Erstinstallation; fordert bei Bedarf das Image-Passwort an
upgrade        Laufzeit aktualisieren, Identität und Token unverändert lassen
server         Managementserver installieren/aktualisieren
service        privilegierten LCS-Systemdienst installieren/aktualisieren
client         grafischen LCS-User-Client installieren/aktualisieren
workstation    Systemdienst + User-Client installieren/aktualisieren
all            Server + Systemdienst + User-Client auf diesem Rechner
reset-identity lokale Geräteidentität explizit löschen (Dienst wird gestoppt)

Optionen:
  --token-file DATEI   Enrollment-Token für einen frischen Systemdienst
  --no-userclient      bei workstation/all nur den Systemdienst installieren;
                       keinen grafischen User-Client installieren

Standardziele:
  Server:       /opt/lcs-server
  Systemdienst: /opt/lcs-service
  User-Client:  /opt/lcs-client

Das Quellverzeichnis (z. B. /opt/lcs als Git-Repository) wird niemals verändert.
Alle Laufzeitpfade können über LCS_*_ROOT bzw. LCS_*_ENV überschrieben werden.
EOF2
}

if [ -z "$MODE" ]; then
   usage
   exit 2
fi

case "$MODE" in
   service|system|client|workstation|all)
      if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
         SERVER_URL="$1"
         shift
      fi
      ;;
esac

while [ $# -gt 0 ]; do
   case "$1" in
      --token-file)
         [ $# -ge 2 ] || { echo "--token-file benötigt eine Datei." >&2; exit 2; }
         TOKEN_SOURCE="$2"
         shift 2
         ;;
      --no-userclient)
         NO_USERCLIENT=1
         shift
         ;;
      *)
         echo "Unbekannte Option: $1" >&2
         usage
         exit 2
         ;;
   esac
done

ensure_server_url() {
   if [ -z "$SERVER_URL" ]; then
      echo "Für $MODE fehlt die Server-URL." >&2
      usage
      exit 2
   fi
}

read_env_value() {
   local file="$1"
   local key="$2"
   [ -f "$file" ] || return 0
   sed -n "s/^${key}=//p" "$file" | tail -n1 | sed -e "s/^[\'\"]//" -e "s/[\'\"]$//"
}

copy_token() {
   local source="$1"
   local target="$2"
   local owner="${3:-root:root}"
   [ -s "$source" ] || return 1
   mkdir -p "$(dirname "$target")"
   install -m 600 "$source" "$target"
   chown "$owner" "$target"
}

apply_server_settings() {
   local response="$1"
   python3 - "$LCS_CLIENT_ENV" "$response" <<'PY'
import json
import os
import sys

path, raw = sys.argv[1:]
settings = json.loads(raw).get('settings', {})
allowed = ('LCS_USER_DATA', 'LCS_REQUIRE_LOCAL_USERNAME')
lines = open(path, encoding='utf-8').read().splitlines() if os.path.exists(path) else []
lines = [line for line in lines if not any(line.startswith(key + '=') for key in allowed)]
for key in allowed:
   value = str(settings.get(key, '')).replace('\r', '').replace('\n', '')
   if value:
      lines.append(key + '=' + value)
with open(path, 'w', encoding='utf-8') as output:
   output.write('\n'.join(lines) + '\n')
PY
}

ensure_enrollment_token() {
   # Bereits enrollte Geräte benötigen bei einem Update keinen Bootstrap-Token.
   if [ -s "$LCS_STATE_ROOT/device.json" ]; then
      return
   fi
   if [ -s "$LCS_ENROLLMENT_TOKEN" ]; then
      chmod 600 "$LCS_ENROLLMENT_TOKEN"
      return
   fi

   if [ -n "$TOKEN_SOURCE" ]; then
      if copy_token "$TOKEN_SOURCE" "$LCS_ENROLLMENT_TOKEN"; then
         return
      fi
      echo "Token-Datei nicht lesbar oder leer: $TOKEN_SOURCE" >&2
      exit 1
   fi

   if [ "$OPERATION" = "install" ] && [ -n "$SERVER_URL" ]; then
      local password response token
      read -r -s -p "Passwort für $(hostname): " password </dev/tty
      echo
      response="$(curl -fsS -H 'Content-Type: application/json' \
         --data "$(python3 -c 'import json,sys; print(json.dumps({"hostname":sys.argv[1],"password":sys.argv[2]}))' "$(hostname)" "$password")" \
         "$SERVER_URL/api/v1/token/claim")" || {
         echo "Token konnte nicht vom Server abgerufen werden." >&2
         exit 1
      }
      token="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["enrollment_token"])')"
      printf '%s\n' "$token" > "$LCS_ENROLLMENT_TOKEN"
      chmod 600 "$LCS_ENROLLMENT_TOKEN"
      apply_server_settings "$response"
      return
   fi

   # Nur Migration: alte v0.4-Ablagen werden gelesen, aber niemals verändert.
   local candidate
   for candidate in \
      /etc/lcs/enrollment.token \
      "$SOURCE_ROOT/.token"; do
      if [ -s "$candidate" ]; then
         copy_token "$candidate" "$LCS_ENROLLMENT_TOKEN"
         echo "Vorhandener Enrollment-Token aus $candidate übernommen."
         return
      fi
   done

   echo "Für einen frischen Systemdienst fehlt der Enrollment-Token." >&2
   echo "Aufruf z. B.: $0 workstation $SERVER_URL --token-file /pfad/server.token" >&2
   exit 1
}

render_template() {
   local src="$1"
   local dst="$2"
   sed \
      -e "s|@SERVER_ROOT@|$LCS_SERVER_ROOT|g" \
      -e "s|@CLIENT_ROOT@|$LCS_CLIENT_ROOT|g" \
      -e "s|@SERVICE_ROOT@|$LCS_SERVICE_ROOT|g" \
      -e "s|@SERVER_ENV@|$LCS_SERVER_ENV|g" \
      -e "s|@CLIENT_ENV@|$LCS_CLIENT_ENV|g" \
      -e "s|@SERVER_USER@|$LCS_SERVER_USER|g" \
      "$src" > "$dst"
}

write_server_env() {
   local old_port old_host old_secret old_discovery old_client_id old_client_secret old_admins
   old_port="$(read_env_value "$LCS_SERVER_ENV" LCS_SERVER_PORT)"
   old_host="$(read_env_value "$LCS_SERVER_ENV" LCS_SERVER_HOST)"
   old_secret="$(read_env_value "$LCS_SERVER_ENV" LCS_SECRET_KEY)"
   old_discovery="$(read_env_value "$LCS_SERVER_ENV" LCS_OIDC_DISCOVERY_URL)"
   old_client_id="$(read_env_value "$LCS_SERVER_ENV" LCS_OIDC_CLIENT_ID)"
   old_client_secret="$(read_env_value "$LCS_SERVER_ENV" LCS_OIDC_CLIENT_SECRET)"
   old_admins="$(read_env_value "$LCS_SERVER_ENV" LCS_ADMIN_USERS)"
   [ -z "$old_port" ] && old_port=5000
   [ -z "$old_host" ] && old_host=127.0.0.1
   [ -z "$old_secret" ] && old_secret="$(openssl rand -hex 32)"

   cat > "$LCS_SERVER_ENV" <<EOF2
LCS_SERVER_DB=$LCS_SERVER_ROOT/data/lcs.sqlite3
LCS_SESSION_TTL=120
LCS_ACTION_LEASE=180
LCS_ACTION_PREFETCH=86400
LCS_SERVER_HOST=$old_host
LCS_SERVER_PORT=$old_port
LCS_RELEASES_DIR=$LCS_SERVER_ROOT/releases
LCS_SOURCE_ROOT=$SOURCE_ROOT
LCS_MANIFEST_FILE=$LCS_SERVER_ROOT/bootstrap-manifest.json
LCS_SECRET_KEY=$old_secret
LCS_OIDC_DISCOVERY_URL=$old_discovery
LCS_OIDC_CLIENT_ID=$old_client_id
LCS_OIDC_CLIENT_SECRET=$old_client_secret
LCS_ADMIN_USERS=$old_admins
LCS_MAX_REQUEST_BYTES=2097152
EOF2
   chmod 600 "$LCS_SERVER_ENV"
   chown root:root "$LCS_SERVER_ENV"
}

migrate_server_data() {
   mkdir -p "$LCS_SERVER_ROOT/data" "$LCS_SERVER_ROOT/releases"

   local db_target="$LCS_SERVER_ROOT/data/lcs.sqlite3"
   if [ ! -f "$db_target" ]; then
      local old
      for old in \
         /opt/lmn-client/server/data/lmn-server.sqlite3 \
         /opt/lmn-client-server/data/lmn-server.sqlite3; do
         if [ -f "$old" ]; then
            cp "$old" "$db_target"
            echo "Bestehende Datenbank aus $old migriert."
            break
         fi
      done
   fi

   if [ ! -s "$LCS_SERVER_ROOT/bootstrap-manifest.json" ]; then
      local old
      for old in \
         /opt/lmn-client/server/bootstrap-manifest.json \
         /opt/lmn-client-server/bootstrap-manifest.json; do
         if [ -f "$old" ]; then
            cp "$old" "$LCS_SERVER_ROOT/bootstrap-manifest.json"
            break
         fi
      done
   fi

   if [ ! -d "$LCS_SERVER_ROOT/releases" ] || [ -z "$(find "$LCS_SERVER_ROOT/releases" -mindepth 1 -print -quit 2>/dev/null)" ]; then
      local old
      for old in /opt/lmn-client/server/releases /opt/lmn-client-server/releases; do
         if [ -d "$old" ]; then
            cp -a "$old/." "$LCS_SERVER_ROOT/releases/" 2>/dev/null || true
            break
         fi
      done
   fi
}

migrate_v04_server_runtime() {
   # v0.4 legte die Server-Konfiguration unter /etc/lcs ab.
   if [ ! -f "$LCS_SERVER_ENV" ] && [ -f /etc/lcs/server.env ]; then
      mkdir -p "$(dirname "$LCS_SERVER_ENV")"
      cp /etc/lcs/server.env "$LCS_SERVER_ENV"
      echo "Server-Konfiguration aus /etc/lcs/server.env übernommen."
   fi
}

migrate_v04_client_runtime() {
   # v0.4 legte Client-Konfiguration und State außerhalb /opt ab.
   if [ ! -f "$LCS_CLIENT_ENV" ] && [ -f /etc/lcs/client.env ]; then
      mkdir -p "$(dirname "$LCS_CLIENT_ENV")"
      cp /etc/lcs/client.env "$LCS_CLIENT_ENV"
      echo "Client-Konfiguration aus /etc/lcs/client.env übernommen."
   fi
   if [ -d /var/lib/lcs ] && [ -z "$(find "$LCS_STATE_ROOT" -mindepth 1 -print -quit 2>/dev/null)" ]; then
      mkdir -p "$LCS_STATE_ROOT"
      cp -a /var/lib/lcs/. "$LCS_STATE_ROOT/"
      echo "Client-State aus /var/lib/lcs übernommen."
   fi
   if [ ! -f "$LCS_ENROLLMENT_TOKEN" ] && [ -s /etc/lcs/enrollment.token ]; then
      copy_token /etc/lcs/enrollment.token "$LCS_ENROLLMENT_TOKEN"
      echo "Enrollment-Token aus /etc/lcs/enrollment.token übernommen."
   fi
}

install_server() {
   systemctl stop lcs-server.service 2>/dev/null || true
   systemctl disable --now lmn-server.service 2>/dev/null || true

   id "$LCS_SERVER_USER" >/dev/null 2>&1 || \
      useradd --system --no-create-home --shell /usr/sbin/nologin "$LCS_SERVER_USER"

   mkdir -p "$LCS_SERVER_ROOT"
   migrate_server_data
   migrate_v04_server_runtime

   find "$LCS_SERVER_ROOT" -mindepth 1 -maxdepth 1 \
      ! -name data ! -name releases ! -name bootstrap-manifest.json ! -name venv \
      ! -name server.env \
      -exec rm -rf {} +
   cp "$SOURCE_ROOT/server/core.py" "$LCS_SERVER_ROOT/"
   cp "$SOURCE_ROOT/server/server.py" "$LCS_SERVER_ROOT/"
   cp "$SOURCE_ROOT/server/lcsctl.py" "$LCS_SERVER_ROOT/"
   cp "$SOURCE_ROOT/server/requirements.txt" "$LCS_SERVER_ROOT/"
   cp "$SOURCE_ROOT/server/server.env.example" "$LCS_SERVER_ROOT/"
   cp -a "$SOURCE_ROOT/server/docs" "$LCS_SERVER_ROOT/"
   cp -a "$SOURCE_ROOT/server/examples" "$LCS_SERVER_ROOT/"
   cp -a "$SOURCE_ROOT/server/web" "$LCS_SERVER_ROOT/"

   if [ ! -f "$LCS_SERVER_ROOT/bootstrap-manifest.json" ]; then
      cp "$SOURCE_ROOT/server/bootstrap-manifest.json" "$LCS_SERVER_ROOT/"
   fi

   if [ ! -d "$LCS_SERVER_ROOT/venv" ]; then
      python3 -m venv "$LCS_SERVER_ROOT/venv"
   fi
   "$LCS_SERVER_ROOT/venv/bin/pip" install -q -r "$LCS_SERVER_ROOT/requirements.txt"

   write_server_env

   chown -R root:root "$LCS_SERVER_ROOT"
   chown -R "$LCS_SERVER_USER:$LCS_SERVER_USER" "$LCS_SERVER_ROOT/data" "$LCS_SERVER_ROOT/releases"
   chown "$LCS_SERVER_USER:$LCS_SERVER_USER" "$LCS_SERVER_ROOT/bootstrap-manifest.json"

   mkdir -p "$LCS_SYSTEMD_ROOT"
   render_template "$SOURCE_ROOT/server/templates/lcs-server.service.in" "$LCS_SYSTEMD_ROOT/lcs-server.service"
   chmod 644 "$LCS_SYSTEMD_ROOT/lcs-server.service"
   rm -f "$LCS_SYSTEMD_ROOT/lmn-server.service"
   systemctl daemon-reload
   systemctl enable --now lcs-server.service

   echo "LCS-Server installiert: $LCS_SERVER_ROOT"
   echo "Konfiguration: $LCS_SERVER_ENV"
}

write_client_env() {
   ensure_server_url
   mkdir -p "$LCS_SERVICE_ROOT"

   local proxy ca user_data require_local_username
   proxy="$(read_env_value "$LCS_CLIENT_ENV" LCS_PROXY)"
   ca="$(read_env_value "$LCS_CLIENT_ENV" LCS_CA_FILE)"
   user_data="$(read_env_value "$LCS_CLIENT_ENV" LCS_USER_DATA)"
   require_local_username="$(read_env_value "$LCS_CLIENT_ENV" LCS_REQUIRE_LOCAL_USERNAME)"
   if [ -z "$proxy" ]; then
      proxy="$(read_env_value /etc/lcs/client.env LCS_PROXY)"
      [ -z "$proxy" ] && proxy="$(read_env_value /etc/lmn-client/client.env LMN_PROXY)"
   fi
   if [ -z "$ca" ]; then
      ca="$(read_env_value /etc/lcs/client.env LCS_CA_FILE)"
      [ -z "$ca" ] && ca="$(read_env_value /etc/lmn-client/client.env LMN_CA_FILE)"
   fi

   cat > "$LCS_CLIENT_ENV" <<EOF2
LCS_SERVER=$SERVER_URL
LCS_HEARTBEAT_SECONDS=20
LCS_POLL_SECONDS=10
LCS_SYNC_SECONDS=60
LCS_STATE_ROOT=$LCS_STATE_ROOT
LCS_FEATURE_ROOT=$LCS_FEATURE_ROOT
LCS_TOKEN_FILE=$LCS_ENROLLMENT_TOKEN
LCS_CHANNEL=stable
EOF2
   [ -n "$proxy" ] && printf 'LCS_PROXY=%s\n' "$proxy" >> "$LCS_CLIENT_ENV"
   [ -n "$ca" ] && printf 'LCS_CA_FILE=%s\n' "$ca" >> "$LCS_CLIENT_ENV"
   [ -n "$user_data" ] && printf 'LCS_USER_DATA=%s\n' "$user_data" >> "$LCS_CLIENT_ENV"
   [ -n "$require_local_username" ] && printf 'LCS_REQUIRE_LOCAL_USERNAME=%s\n' "$require_local_username" >> "$LCS_CLIENT_ENV"
   chmod 644 "$LCS_CLIENT_ENV"
   chown root:root "$LCS_CLIENT_ENV"
}

install_service() {
   ensure_server_url

   systemctl stop lcs-service.service 2>/dev/null || true
   systemctl disable --now lmn-agent.service 2>/dev/null || true

   mkdir -p "$LCS_SERVICE_ROOT" "$LCS_FEATURE_ROOT" "$LCS_STATE_ROOT"
   migrate_v04_client_runtime

   find "$LCS_SERVICE_ROOT" -mindepth 1 -maxdepth 1 \
      ! -name features ! -name state ! -name venv ! -name client.env ! -name enrollment.token \
      -exec rm -rf {} +
   cp -a "$SOURCE_ROOT/system/." "$LCS_SERVICE_ROOT/"
   rm -rf "$LCS_SERVICE_ROOT/linux" "$LCS_SERVICE_ROOT/venv"
   [ -d "$LCS_SERVICE_ROOT/venv" ] || python3 -m venv "$LCS_SERVICE_ROOT/venv"

   write_client_env
   ensure_enrollment_token

   mkdir -p "$LCS_SYSTEMD_ROOT"
   render_template "$SOURCE_ROOT/system/linux/lcs-service.service.in" "$LCS_SYSTEMD_ROOT/lcs-service.service"
   chmod 644 "$LCS_SYSTEMD_ROOT/lcs-service.service"
   rm -f "$LCS_SYSTEMD_ROOT/lmn-agent.service"
   systemctl daemon-reload
   systemctl enable lcs-service.service

   systemctl start lcs-service.service
   echo "LCS-Systemdienst wurde gestartet."

   echo "LCS-Systemdienst installiert und aktiviert."
   echo "Runtime: $LCS_SERVICE_ROOT"
   echo "Konfiguration: $LCS_CLIENT_ENV"
   echo "State: $LCS_STATE_ROOT"
}

install_client() {
   ensure_server_url
   mkdir -p "$LCS_CLIENT_ROOT" "$LCS_SERVICE_ROOT" "$LCS_AUTOSTART_ROOT" "$LCS_APPLICATIONS_ROOT"
   rm -rf "$LCS_CLIENT_ROOT"/*
   cp -a "$SOURCE_ROOT/client/." "$LCS_CLIENT_ROOT/"
   rm -rf "$LCS_CLIENT_ROOT/linux" "$LCS_CLIENT_ROOT/venv"
   python3 -m venv "$LCS_CLIENT_ROOT/venv"
   "$LCS_CLIENT_ROOT/venv/bin/pip" install -q -r "$LCS_CLIENT_ROOT/requirements.txt"

   if ! python3 -c "import tkinter" >/dev/null 2>&1; then
      echo "Hinweis: python3-tk fehlt. Vor dem Imaging installieren: apt install python3-tk" >&2
   fi

   write_client_env

   # Autostart nach Benutzeranmeldung.
   render_template "$SOURCE_ROOT/client/linux/lcs-client.desktop.in" "$LCS_AUTOSTART_ROOT/lcs-client.desktop"
   chmod 644 "$LCS_AUTOSTART_ROOT/lcs-client.desktop"
   rm -f "$LCS_AUTOSTART_ROOT/lmn-user-client.desktop"

   # Sichtbarer Starter im Anwendungsmenü.
   render_template "$SOURCE_ROOT/client/linux/lcs-client-menu.desktop.in" "$LCS_APPLICATIONS_ROOT/lcs-client.desktop"
   chmod 644 "$LCS_APPLICATIONS_ROOT/lcs-client.desktop"

   echo "LCS-User-Client installiert: $LCS_CLIENT_ROOT"
   echo "Autostart: $LCS_AUTOSTART_ROOT/lcs-client.desktop"
   echo "Menüeintrag: $LCS_APPLICATIONS_ROOT/lcs-client.desktop"
}

remove_client_integration() {
   rm -f "$LCS_AUTOSTART_ROOT/lcs-client.desktop"
   rm -f "$LCS_AUTOSTART_ROOT/lmn-user-client.desktop"
   rm -f "$LCS_APPLICATIONS_ROOT/lcs-client.desktop"
}

reset_identity() {
   systemctl stop lcs-service.service 2>/dev/null || true
   rm -f \
      "$LCS_STATE_ROOT/device.json" \
      "$LCS_STATE_ROOT/device-public.json" \
      "$LCS_STATE_ROOT/scheduler.json" \
      "$LCS_STATE_ROOT/pending-actions.json" \
      "$LCS_STATE_ROOT/result-outbox.json" \
      "$LCS_STATE_ROOT/event-outbox.json"
   rm -rf "$LCS_FEATURE_ROOT/packages"
   rm -f "$LCS_FEATURE_ROOT/stack.json"
   echo "Lokale LCS-Geräteidentität und Capability-Cache wurden gelöscht."
   echo "Der Dienst bleibt enabled, ist aber bis zum nächsten Start gestoppt."
}

case "$MODE" in
   server)
      install_server
      ;;
   service|system)
      install_service
      ;;
   client)
      install_client
      ;;
   workstation)
      install_service
      if [ "$NO_USERCLIENT" -eq 1 ]; then
         remove_client_integration
         echo "LCS-User-Client wurde wegen --no-userclient nicht installiert."
      else
         install_client
      fi
      ;;
   all)
      ensure_server_url
      install_server
      install_service
      if [ "$NO_USERCLIENT" -eq 1 ]; then
         remove_client_integration
         echo "LCS-User-Client wurde wegen --no-userclient nicht installiert."
      else
         install_client
      fi
      ;;
   reset-identity)
      reset_identity
      ;;
   *)
      usage
      exit 2
      ;;
esac
