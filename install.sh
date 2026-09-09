#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-}"
SERVER_URL="${2:-}"

LCS_SERVER_ROOT="${LCS_SERVER_ROOT:-/opt/lcs-server}"
LCS_CLIENT_ROOT="${LCS_CLIENT_ROOT:-/opt/lcs-client}"
LCS_SERVICE_ROOT="${LCS_SERVICE_ROOT:-/opt/lcs-service}"
LCS_CONFIG_ROOT="${LCS_CONFIG_ROOT:-/etc/lcs}"
LCS_STATE_ROOT="${LCS_STATE_ROOT:-/var/lib/lcs}"
LCS_FEATURE_ROOT="${LCS_FEATURE_ROOT:-$LCS_SERVICE_ROOT/features}"
LCS_SERVER_USER="${LCS_SERVER_USER:-lcs}"
LCS_SERVER_ENV="${LCS_SERVER_ENV:-$LCS_CONFIG_ROOT/server.env}"
LCS_CLIENT_ENV="${LCS_CLIENT_ENV:-$LCS_CONFIG_ROOT/client.env}"
LCS_SERVER_TOKEN="${LCS_SERVER_TOKEN:-$LCS_CONFIG_ROOT/server.token}"
LCS_ENROLLMENT_TOKEN="${LCS_ENROLLMENT_TOKEN:-$LCS_CONFIG_ROOT/enrollment.token}"

if [ "$(id -u)" -ne 0 ]; then
   echo "Bitte als root ausführen." >&2
   exit 1
fi

usage() {
   cat <<EOF2
Aufruf:
  $0 server
  $0 service https://clients.example
  $0 client https://clients.example
  $0 workstation https://clients.example
  $0 all https://clients.example
  $0 reset-identity

server         Managementserver installieren/aktualisieren
service        privilegierten LCS-Systemdienst installieren/aktualisieren
client         grafischen LCS-User-Client installieren/aktualisieren
workstation    Systemdienst + User-Client installieren/aktualisieren
all            Server + Systemdienst + User-Client auf diesem Rechner
reset-identity lokale Geräteidentität explizit löschen (Dienst wird gestoppt)

Installationsziele können über LCS_SERVER_ROOT, LCS_CLIENT_ROOT,
LCS_SERVICE_ROOT, LCS_CONFIG_ROOT und LCS_STATE_ROOT überschrieben werden.
EOF2
}

if [ -z "$MODE" ]; then
   usage
   exit 2
fi

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
   sed -n "s/^${key}=//p" "$file" | tail -n1 | sed 's/^['\"'\"']\(.*\)['\"'\"']$/\1/'
}

prepare_package_token() {
   if [ -s "$ROOT/.token" ]; then
      chmod 600 "$ROOT/.token"
      return
   fi

   local legacy=""
   for candidate in \
      /etc/lcs/server.token \
      /etc/lmn-client-server/.token \
      /opt/lmn-client-server/server.env \
      /opt/lmn-client/server/server.env \
      /etc/lmn-client/client.env; do
      if [ -f "$candidate" ]; then
         if [[ "$candidate" == *.env ]]; then
            legacy="$(read_env_value "$candidate" LMN_ENROLLMENT_TOKEN)"
         else
            legacy="$(cat "$candidate" 2>/dev/null || true)"
         fi
         [ -n "$legacy" ] && break
      fi
   done

   if [ -n "$legacy" ]; then
      printf '%s\n' "$legacy" > "$ROOT/.token"
      chmod 600 "$ROOT/.token"
      echo "Vorhandener Enrollment-Token wurde nach $ROOT/.token migriert."
      return
   fi

   openssl rand -hex 32 > "$ROOT/.token"
   chmod 600 "$ROOT/.token"
   echo "Neuer Enrollment-Token wurde in $ROOT/.token erzeugt."
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
   mkdir -p "$LCS_CONFIG_ROOT"
   local old_port="$(read_env_value "$LCS_SERVER_ENV" LCS_SERVER_PORT)"
   local old_host="$(read_env_value "$LCS_SERVER_ENV" LCS_SERVER_HOST)"
   [ -z "$old_port" ] && old_port=5000
   [ -z "$old_host" ] && old_host=127.0.0.1

   cat > "$LCS_SERVER_ENV" <<EOF2
LCS_SERVER_DB=$LCS_SERVER_ROOT/data/lcs.sqlite3
LCS_SESSION_TTL=120
LCS_ACTION_LEASE=180
LCS_ACTION_PREFETCH=86400
LCS_SERVER_HOST=$old_host
LCS_SERVER_PORT=$old_port
LCS_RELEASES_DIR=$LCS_SERVER_ROOT/releases
LCS_MANIFEST_FILE=$LCS_SERVER_ROOT/bootstrap-manifest.json
LCS_TOKEN_FILE=$LCS_SERVER_TOKEN
EOF2
   chmod 600 "$LCS_SERVER_ENV"
}

migrate_server_data() {
   mkdir -p "$LCS_SERVER_ROOT/data" "$LCS_SERVER_ROOT/releases"

   local db_target="$LCS_SERVER_ROOT/data/lcs.sqlite3"
   if [ ! -f "$db_target" ]; then
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
      for old in /opt/lmn-client/server/releases /opt/lmn-client-server/releases; do
         if [ -d "$old" ]; then
            mkdir -p "$LCS_SERVER_ROOT/releases"
            cp -a "$old/." "$LCS_SERVER_ROOT/releases/" 2>/dev/null || true
            break
         fi
      done
   fi
}

install_server() {
   prepare_package_token
   systemctl stop lcs-server.service 2>/dev/null || true
   systemctl disable --now lmn-server.service 2>/dev/null || true

   id "$LCS_SERVER_USER" >/dev/null 2>&1 || \
      useradd --system --no-create-home --shell /usr/sbin/nologin "$LCS_SERVER_USER"

   mkdir -p "$LCS_SERVER_ROOT" "$LCS_CONFIG_ROOT"
   migrate_server_data

   find "$LCS_SERVER_ROOT" -mindepth 1 -maxdepth 1 \
      ! -name data ! -name releases ! -name bootstrap-manifest.json ! -name venv \
      -exec rm -rf {} +
   cp "$ROOT/server/core.py" "$LCS_SERVER_ROOT/"
   cp "$ROOT/server/server.py" "$LCS_SERVER_ROOT/"
   cp "$ROOT/server/lcsctl.py" "$LCS_SERVER_ROOT/"
   cp "$ROOT/server/requirements.txt" "$LCS_SERVER_ROOT/"
   cp "$ROOT/server/server.env.example" "$LCS_SERVER_ROOT/"
   cp -a "$ROOT/server/docs" "$LCS_SERVER_ROOT/"
   cp -a "$ROOT/server/examples" "$LCS_SERVER_ROOT/"

   if [ ! -f "$LCS_SERVER_ROOT/bootstrap-manifest.json" ]; then
      cp "$ROOT/server/bootstrap-manifest.json" "$LCS_SERVER_ROOT/"
   fi

   if [ ! -d "$LCS_SERVER_ROOT/venv" ]; then
      python3 -m venv "$LCS_SERVER_ROOT/venv"
   fi
   "$LCS_SERVER_ROOT/venv/bin/pip" install -q -r "$LCS_SERVER_ROOT/requirements.txt"

   write_server_env
   ln -sfn "$LCS_SERVER_ENV" "$LCS_SERVER_ROOT/server.env"
   cp "$ROOT/.token" "$LCS_SERVER_TOKEN"
   chmod 600 "$LCS_SERVER_TOKEN"

   chown -R root:root "$LCS_SERVER_ROOT"
   chown -R "$LCS_SERVER_USER:$LCS_SERVER_USER" "$LCS_SERVER_ROOT/data" "$LCS_SERVER_ROOT/releases"
   chown "$LCS_SERVER_USER:$LCS_SERVER_USER" "$LCS_SERVER_ROOT/bootstrap-manifest.json"

   render_template "$ROOT/server/templates/lcs-server.service.in" /etc/systemd/system/lcs-server.service
   chmod 644 /etc/systemd/system/lcs-server.service
   rm -f /etc/systemd/system/lmn-server.service
   systemctl daemon-reload
   systemctl enable --now lcs-server.service

   echo "LCS-Server installiert: $LCS_SERVER_ROOT"
   echo "Konfiguration: $LCS_SERVER_ENV"
   echo "Enrollment-Token: $LCS_SERVER_TOKEN"
}

write_client_env() {
   ensure_server_url
   mkdir -p "$LCS_CONFIG_ROOT"

   local proxy="$(read_env_value "$LCS_CLIENT_ENV" LCS_PROXY)"
   local ca="$(read_env_value "$LCS_CLIENT_ENV" LCS_CA_FILE)"
   if [ -z "$proxy" ]; then
      proxy="$(read_env_value /etc/lmn-client/client.env LMN_PROXY)"
   fi
   if [ -z "$ca" ]; then
      ca="$(read_env_value /etc/lmn-client/client.env LMN_CA_FILE)"
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
   chmod 644 "$LCS_CLIENT_ENV"
}

install_service() {
   ensure_server_url
   prepare_package_token

   systemctl stop lcs-service.service 2>/dev/null || true
   systemctl disable --now lmn-agent.service 2>/dev/null || true

   mkdir -p "$LCS_SERVICE_ROOT" "$LCS_FEATURE_ROOT" "$LCS_STATE_ROOT" "$LCS_CONFIG_ROOT"
   find "$LCS_SERVICE_ROOT" -mindepth 1 -maxdepth 1 ! -name features ! -name venv -exec rm -rf {} +
   cp -a "$ROOT/system/." "$LCS_SERVICE_ROOT/"
   rm -rf "$LCS_SERVICE_ROOT/linux" "$LCS_SERVICE_ROOT/venv"
   python3 -m venv "$LCS_SERVICE_ROOT/venv"

   write_client_env
   cp "$ROOT/.token" "$LCS_ENROLLMENT_TOKEN"
   chmod 600 "$LCS_ENROLLMENT_TOKEN"

   render_template "$ROOT/system/linux/lcs-service.service.in" /etc/systemd/system/lcs-service.service
   chmod 644 /etc/systemd/system/lcs-service.service
   rm -f /etc/systemd/system/lmn-agent.service
   systemctl daemon-reload
   systemctl enable lcs-service.service

   # Auf einem Masterimage bleibt nur die Kopie unter /etc/lcs erhalten.
   # So landet der globale Bootstrap-Token nicht zusätzlich im Paketbaum des Images.
   if [ "${LCS_KEEP_PACKAGE_TOKEN:-0}" != "1" ]; then
      rm -f "$ROOT/.token"
   fi

   echo "LCS-Systemdienst installiert und aktiviert, aber absichtlich NICHT gestartet."
   echo "Runtime: $LCS_SERVICE_ROOT"
   echo "State: $LCS_STATE_ROOT"
}

install_client() {
   ensure_server_url
   mkdir -p "$LCS_CLIENT_ROOT" "$LCS_CONFIG_ROOT" /etc/xdg/autostart
   rm -rf "$LCS_CLIENT_ROOT"/*
   cp -a "$ROOT/client/." "$LCS_CLIENT_ROOT/"
   rm -rf "$LCS_CLIENT_ROOT/linux" "$LCS_CLIENT_ROOT/venv"
   python3 -m venv "$LCS_CLIENT_ROOT/venv"

   if ! python3 -c "import tkinter" >/dev/null 2>&1; then
      echo "Hinweis: python3-tk fehlt. Vor dem Imaging installieren: apt install python3-tk" >&2
   fi

   write_client_env
   render_template "$ROOT/client/linux/lcs-client.desktop.in" /etc/xdg/autostart/lcs-client.desktop
   chmod 644 /etc/xdg/autostart/lcs-client.desktop
   rm -f /etc/xdg/autostart/lmn-user-client.desktop

   echo "LCS-User-Client installiert: $LCS_CLIENT_ROOT"
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
      # install_service entfernt .token standardmäßig; der Client benötigt ihn nicht.
      install_client
      ;;
   all)
      ensure_server_url
      # Bei all muss der Paket-Token bis nach Installation des Dienstes erhalten bleiben.
      export LCS_KEEP_PACKAGE_TOKEN=1
      install_server
      install_service
      install_client
      ;;
   reset-identity)
      reset_identity
      ;;
   *)
      usage
      exit 2
      ;;
esac
