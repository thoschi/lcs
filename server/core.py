import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('LCS_SERVER_DB', str(BASE / 'data/lcs.sqlite3')))
SESSION_TTL = int(os.environ.get('LCS_SESSION_TTL', '120'))
ACTION_LEASE = int(os.environ.get('LCS_ACTION_LEASE', '180'))
ACTION_PREFETCH = int(os.environ.get('LCS_ACTION_PREFETCH', '86400'))


def now_ts():
   return int(time.time())


def db():
   conn = sqlite3.connect(DB_PATH)
   conn.row_factory = sqlite3.Row
   return conn


def _create_devices_table(conn, table='devices'):
   conn.execute(f'''
      CREATE TABLE IF NOT EXISTS {table} (
         id TEXT PRIMARY KEY,
         token_hash TEXT NOT NULL,
         hostname TEXT NOT NULL,
         platform TEXT,
         agent_version TEXT,
         hardware_json TEXT,
         logged_in_users_json TEXT,
         stack_generation INTEGER NOT NULL DEFAULT 0,
         first_seen INTEGER NOT NULL,
         last_seen INTEGER NOT NULL,
         is_image_source INTEGER NOT NULL DEFAULT 0,
         settings_json TEXT NOT NULL DEFAULT '{{}}',
         template_device_id TEXT NOT NULL DEFAULT ''
      )
   ''')


def _migrate_devices(conn):
   exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='devices'").fetchone()
   if not exists:
      return
   columns = {row['name'] for row in conn.execute('PRAGMA table_info(devices)').fetchall()}
   expected = {'id', 'token_hash', 'hostname', 'platform', 'agent_version', 'hardware_json',
               'logged_in_users_json', 'stack_generation', 'first_seen', 'last_seen',
               'is_image_source', 'settings_json', 'template_device_id'}
   if columns == expected:
      return
   conn.execute('DROP TABLE IF EXISTS devices_v03')
   _create_devices_table(conn, 'devices_v03')
   image_source = 'is_image_source' if 'is_image_source' in columns else '0'
   settings = 'settings_json' if 'settings_json' in columns else "'{}'"
   template = 'template_device_id' if 'template_device_id' in columns else "''"
   conn.execute(f'''
      INSERT INTO devices_v03(
         id, token_hash, hostname, platform, agent_version,
         hardware_json, logged_in_users_json, stack_generation, first_seen, last_seen,
         is_image_source, settings_json, template_device_id
      )
      SELECT id, token_hash, hostname, platform, agent_version,
         hardware_json, logged_in_users_json, COALESCE(stack_generation, 0), first_seen, last_seen,
         {image_source}, {settings}, {template}
      FROM devices
   ''')
   conn.execute('DROP TABLE devices')
   conn.execute('ALTER TABLE devices_v03 RENAME TO devices')


def init_db():
   DB_PATH.parent.mkdir(parents=True, exist_ok=True)
   with db() as conn:
      conn.execute('PRAGMA foreign_keys=OFF')
      _migrate_devices(conn)
      _create_devices_table(conn)
      conn.executescript('''
      CREATE TABLE IF NOT EXISTS users (
         username TEXT PRIMARY KEY,
         full_name TEXT,
         password_hash TEXT NOT NULL,
         enabled INTEGER NOT NULL DEFAULT 1
      );
      CREATE TABLE IF NOT EXISTS sessions (
         token_hash TEXT PRIMARY KEY,
         device_id TEXT NOT NULL,
         username TEXT NOT NULL,
         user_client_version TEXT,
         created_at INTEGER NOT NULL,
         last_seen INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS actions (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         device_id TEXT NOT NULL,
         capability_id TEXT NOT NULL,
         parameters_json TEXT NOT NULL DEFAULT '{}',
         run_at INTEGER NOT NULL,
         status TEXT NOT NULL DEFAULT 'queued',
         lease_until INTEGER,
         created_at INTEGER NOT NULL,
         started_at INTEGER,
         finished_at INTEGER,
         result_json TEXT
      );
      CREATE TABLE IF NOT EXISTS events (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         device_id TEXT,
         username TEXT,
         source TEXT NOT NULL,
         event_type TEXT NOT NULL,
         capability_id TEXT,
         payload_json TEXT NOT NULL DEFAULT '{}',
         created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS device_audit_log (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         device_id TEXT NOT NULL,
         hostname TEXT NOT NULL,
         event_type TEXT NOT NULL,
         old_token_hash TEXT NOT NULL DEFAULT '',
         new_token_hash TEXT NOT NULL DEFAULT '',
         created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS device_groups (
         group_name TEXT NOT NULL,
         device_id TEXT NOT NULL,
         PRIMARY KEY(group_name, device_id)
      );
      CREATE TABLE IF NOT EXISTS groups (
         name TEXT PRIMARY KEY,
         description TEXT NOT NULL DEFAULT ''
      );
      CREATE TABLE IF NOT EXISTS capability_assignments (
         capability_id TEXT NOT NULL,
         target_type TEXT NOT NULL,
         target_id TEXT NOT NULL,
         enabled INTEGER NOT NULL DEFAULT 1,
         PRIMARY KEY(capability_id, target_type, target_id)
      );
      CREATE TABLE IF NOT EXISTS action_templates (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         group_name TEXT NOT NULL,
         capability_id TEXT NOT NULL,
         parameters_json TEXT NOT NULL DEFAULT '{}',
         scope TEXT NOT NULL DEFAULT 'system',
         username TEXT NOT NULL DEFAULT '',
         created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS enrollment_tokens (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         name TEXT NOT NULL UNIQUE,
         token_hash TEXT NOT NULL UNIQUE,
         token_prefix TEXT NOT NULL,
         token_value TEXT NOT NULL DEFAULT '',
         enabled INTEGER NOT NULL DEFAULT 1,
         created_at INTEGER NOT NULL,
         last_used_at INTEGER,
         enrollment_count INTEGER NOT NULL DEFAULT 0
      );
      ''')
      columns = {row['name'] for row in conn.execute('PRAGMA table_info(devices)').fetchall()}
      if 'stack_generation' not in columns:
         conn.execute('ALTER TABLE devices ADD COLUMN stack_generation INTEGER NOT NULL DEFAULT 0')
      if 'is_image_source' not in columns:
         conn.execute('ALTER TABLE devices ADD COLUMN is_image_source INTEGER NOT NULL DEFAULT 0')
      if 'settings_json' not in columns:
         conn.execute("ALTER TABLE devices ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
      if 'template_device_id' not in columns:
         conn.execute("ALTER TABLE devices ADD COLUMN template_device_id TEXT NOT NULL DEFAULT ''")
      action_columns = {row['name'] for row in conn.execute('PRAGMA table_info(actions)').fetchall()}
      if 'scope' not in action_columns:
         conn.execute("ALTER TABLE actions ADD COLUMN scope TEXT NOT NULL DEFAULT 'system'")
      if 'username' not in action_columns:
         conn.execute("ALTER TABLE actions ADD COLUMN username TEXT NOT NULL DEFAULT ''")
      token_columns = {row['name'] for row in conn.execute('PRAGMA table_info(enrollment_tokens)').fetchall()}
      if 'hostname' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN hostname TEXT NOT NULL DEFAULT ''")
      if 'password_hash' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
      if 'settings_json' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
      if 'token_type' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN token_type TEXT NOT NULL DEFAULT 'template'")
      if 'template_device_id' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN template_device_id TEXT NOT NULL DEFAULT ''")
      if 'group_name' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
      if 'token_value' not in token_columns:
         conn.execute("ALTER TABLE enrollment_tokens ADD COLUMN token_value TEXT NOT NULL DEFAULT ''")
      assignment_columns = {row['name'] for row in conn.execute('PRAGMA table_info(capability_assignments)').fetchall()}
      if 'execution' not in assignment_columns:
         conn.execute("ALTER TABLE capability_assignments ADD COLUMN execution TEXT NOT NULL DEFAULT 'manual'")
      conn.execute('''UPDATE enrollment_tokens SET template_device_id=COALESCE((
         SELECT id FROM devices
         WHERE devices.is_image_source=1 AND lower(devices.hostname)=lower(enrollment_tokens.hostname)
         LIMIT 1), '')
         WHERE hostname<>'' AND template_device_id='' ''')
      for row in conn.execute("SELECT id, name FROM enrollment_tokens WHERE group_name='' ").fetchall():
         group_name = 'Enrollment: ' + row['name']
         conn.execute('INSERT OR IGNORE INTO groups(name, description) VALUES(?,?)',
                      (group_name, 'Automatisch für Enrollment-Zugang ' + row['name']))
         conn.execute('UPDATE enrollment_tokens SET group_name=? WHERE id=?', (group_name, row['id']))


def token_hash(token):
   return hashlib.sha256(token.encode('utf-8')).hexdigest()


def password_hash(password, salt=None):
   salt = salt or secrets.token_bytes(16)
   digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=128*1024*1024)
   return 'scrypt$%s$%s' % (salt.hex(), digest.hex())


def verify_password(password, stored):
   try:
      _, salt_hex, digest_hex = stored.split('$', 2)
      expected = bytes.fromhex(digest_hex)
      actual = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32, maxmem=128*1024*1024)
      return hmac.compare_digest(actual, expected)
   except Exception:
      return False


def enrollment_settings(user_data='', require_local_username=False, password_username=''):
   settings = {}
   user_data = str(user_data).strip()
   if '\n' in user_data or '\r' in user_data:
      raise ValueError('Benutzerdatenpfad darf keinen Zeilenumbruch enthalten')
   if user_data:
      settings['LCS_USER_DATA'] = user_data
   settings['LCS_REQUIRE_LOCAL_USERNAME'] = 'true' if require_local_username else 'false'
   password_username = str(password_username).strip()
   if '\n' in password_username or '\r' in password_username:
      raise ValueError('Systembenutzername darf keinen Zeilenumbruch enthalten')
   if password_username:
      settings['LCS_PASSWORD_USERNAME'] = password_username
   return settings


def add_enrollment_token(name, password='', template=True, token=None, settings=None, hostname=''):
   name = str(name).strip()
   if not name:
      raise ValueError('Token-Name fehlt')
   if not password:
      raise ValueError('Passwort fehlt')
   hostname = str(hostname).strip().lower()
   if '\n' in hostname or '\r' in hostname:
      raise ValueError('Hostname darf keinen Zeilenumbruch enthalten')
   material = name + '\0' + ((hostname + '\0') if hostname else '') + str(password)
   token = token or hashlib.sha256(material.encode('utf-8')).hexdigest()
   group_name = 'Enrollment: ' + name
   with db() as conn:
      conn.execute('INSERT OR IGNORE INTO groups(name, description) VALUES(?,?)',
                   (group_name, 'Automatisch für Enrollment-Zugang ' + name))
      conn.execute('''
         INSERT INTO enrollment_tokens(
            name, token_hash, token_prefix, created_at, hostname, password_hash, settings_json, token_type, group_name,
            token_value
         ) VALUES(?,?,?,?,?,?,?,?,?,?)
      ''', (name, token_hash(token), token[:8], now_ts(), hostname, password_hash(password),
            json.dumps(settings or {}, ensure_ascii=False), 'template' if template else 'single', group_name, token))
   return token


def claim_enrollment_token(hostname, password):
   hostname = str(hostname).strip().lower()
   with db() as conn:
      rows = conn.execute('SELECT * FROM enrollment_tokens WHERE enabled=1').fetchall()
   matches = [row for row in rows if verify_password(str(password), row['password_hash']) and
              (not row['hostname'] or row['hostname'].lower() == hostname)]
   if len(matches) != 1:
      return 403, {'error': 'Passwort ist ungültig oder nicht eindeutig'}
   row = matches[0]
   # Bestehende, hostnamegebundene Zugänge bleiben während der Migration nutzbar.
   material = row['name'] + '\0' + ((row['hostname'] + '\0') if row['hostname'] else '') + str(password)
   token = hashlib.sha256(material.encode('utf-8')).hexdigest()
   if not row['token_value']:
      with db() as conn:
         conn.execute('UPDATE enrollment_tokens SET token_value=? WHERE id=?', (token, row['id']))
   try:
      settings = json.loads(row['settings_json'] or '{}')
   except (json.JSONDecodeError, TypeError):
      settings = {}
   return 200, {'enrollment_token': token, 'settings': settings}


def check_enrollment_token(supplied_hash, hostname=''):
   with db() as conn:
      token = conn.execute(
         'SELECT 1 FROM enrollment_tokens WHERE token_hash=? AND enabled=1',
         (str(supplied_hash),)).fetchone()
      template = conn.execute('''
         SELECT 1 FROM enrollment_tokens
         WHERE enabled=1 AND token_type='template' AND (hostname='' OR lower(hostname)=lower(?))
      ''', (str(hostname).strip(),)).fetchone()
   return 200, {'valid': bool(token), 'template_available': bool(template)}


def create_reenrollment_token(device_id):
   with db() as conn:
      token = conn.execute('''
         SELECT et.token_value FROM devices d
         JOIN enrollment_tokens et ON et.template_device_id=d.template_device_id
         WHERE d.id=? AND et.enabled=1 AND et.token_type='template'
      ''', (device_id,)).fetchone()
   if not token or not token['token_value']:
      raise ValueError('no active template token for device: ' + device_id)
   return token['token_value']


def enroll(payload):
   hostname = str(payload.get('hostname', '')).strip()
   supplied_token = str(payload.get('enrollment_token', ''))
   if not hostname:
      return 400, {'error': 'hostname required'}

   supplied_hash = token_hash(supplied_token) if supplied_token else ''
   with db() as conn:
      reusable = conn.execute('''
         SELECT id, hostname, settings_json, token_type, template_device_id, group_name
         FROM enrollment_tokens WHERE token_hash=? AND enabled=1
      ''', (supplied_hash,)).fetchone()
      if not reusable:
         return 403, {'error': 'invalid enrollment token'}
      existing = None
      if reusable['token_type'] == 'template' and reusable['template_device_id']:
         existing = conn.execute('''
         SELECT id, hostname, settings_json, template_device_id, is_image_source, token_hash
         FROM devices WHERE lower(hostname)=lower(?) AND
            (id=? OR template_device_id=?)
         ''', (hostname, reusable['template_device_id'], reusable['template_device_id'])).fetchone()
      device_id = existing['id'] if existing else secrets.token_hex(8)
      registered_hostname = existing['hostname'] if existing else hostname
      settings_json = (existing['settings_json'] if existing else
                       reusable['settings_json'] if reusable else '{}')
      template_device_id = (existing['template_device_id'] if existing else
                            reusable['template_device_id'] if reusable else '')
      image_source = bool(existing['is_image_source']) if existing else bool(
         reusable and reusable['token_type'] == 'template' and
         reusable['template_device_id'] in ('', device_id))
      device_token = secrets.token_urlsafe(32)
      new_token_hash = token_hash(device_token)
      now = now_ts()
      conn.execute('''
         INSERT INTO devices(id, token_hash, hostname, platform, agent_version, first_seen, last_seen, is_image_source, settings_json, template_device_id)
         VALUES(?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET
            token_hash=excluded.token_hash,
            hostname=excluded.hostname,
            platform=excluded.platform,
            agent_version=excluded.agent_version,
            last_seen=excluded.last_seen,
            is_image_source=excluded.is_image_source,
            settings_json=excluded.settings_json,
            template_device_id=excluded.template_device_id
      ''', (
         device_id, new_token_hash, registered_hostname, payload.get('platform', ''),
         payload.get('agent_version', ''), now, now,
         int(image_source), settings_json, template_device_id
      ))
      if reusable:
         if reusable['token_type'] == 'single':
            _apply_enrollment_group(conn, device_id, reusable['group_name'], now)
            conn.execute('DELETE FROM enrollment_tokens WHERE id=?', (reusable['id'],))
         elif not reusable['template_device_id']:
            conn.execute('''UPDATE enrollment_tokens SET template_device_id=?, last_used_at=?,
               enrollment_count=enrollment_count+1 WHERE id=?''', (device_id, now, reusable['id']))
         elif reusable['template_device_id'] != device_id:
            conn.execute('''UPDATE enrollment_tokens SET last_used_at=?, enrollment_count=enrollment_count+1
               WHERE id=?''', (now, reusable['id']))
            _apply_enrollment_group(conn, device_id, reusable['group_name'], now)
      log_event(conn, device_id, '', 'system', 'enroll', '', {'hostname': registered_hostname})
      conn.execute('''INSERT INTO device_audit_log(
         device_id, hostname, event_type, old_token_hash, new_token_hash, created_at)
         VALUES(?,?,?,?,?,?)''', (device_id, registered_hostname,
         'reregistered' if existing else 'registered',
         existing['token_hash'] if existing else '', new_token_hash, now))
      if existing and not hmac.compare_digest(existing['token_hash'], new_token_hash):
         conn.execute('''INSERT INTO device_audit_log(
            device_id, hostname, event_type, old_token_hash, new_token_hash, created_at)
            VALUES(?,?,?,?,?,?)''', (device_id, registered_hostname, 'token_changed',
            existing['token_hash'], new_token_hash, now))
   settings = {}
   if settings_json:
      try:
         settings = json.loads(settings_json)
      except (json.JSONDecodeError, TypeError):
         pass
   return 200, {'device_id': device_id, 'device_token': device_token,
                'image_source': image_source, 'settings': settings,
                'hostname': registered_hostname}


def _apply_enrollment_group(conn, device_id, group_name, now):
   conn.execute('INSERT OR IGNORE INTO device_groups(group_name, device_id) VALUES(?,?)',
                (group_name, device_id))
   conn.execute('''INSERT INTO actions(
      device_id, capability_id, parameters_json, run_at, status, created_at, scope, username)
      SELECT ?, capability_id, parameters_json, ?, 'queued', ?, scope, username
      FROM action_templates WHERE group_name=?''', (device_id, now, now, group_name))


def authenticate_device(device_id, token):
   if not device_id or not token:
      return None
   with db() as conn:
      row = conn.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
   if not row or not hmac.compare_digest(row['token_hash'], token_hash(token)):
      return None
   return row


def _device_is_template(conn, device_id, hostname):
   return conn.execute('''
      SELECT 1 FROM enrollment_tokens
      WHERE enabled=1 AND token_type='template'
         AND (template_device_id=? OR (hostname<>'' AND lower(hostname)=lower(?)))
      LIMIT 1
   ''', (device_id, hostname)).fetchone() is not None


def heartbeat(device_id, token, payload):
   device = authenticate_device(device_id, token)
   if not device:
      return 401, {'error': 'unauthorized'}
   hardware = payload.get('hardware', {})
   hostname = str(hardware.get('hostname') or device['hostname'])
   with db() as conn:
      conn.execute('''UPDATE enrollment_tokens SET template_device_id=?
         WHERE enabled=1 AND token_type='template' AND template_device_id=''
            AND hostname<>'' AND lower(hostname)=lower(?)''', (device['id'], hostname))
      image_source = _device_is_template(conn, device['id'], hostname)
      conn.execute('''
         UPDATE devices SET last_seen=?, hostname=?, agent_version=?, hardware_json=?,
            logged_in_users_json=?, stack_generation=?, is_image_source=? WHERE id=?
      ''', (
         now_ts(), hostname, payload.get('agent_version', ''),
         json.dumps(hardware, ensure_ascii=False),
         json.dumps(payload.get('logged_in_users', []), ensure_ascii=False),
         int(payload.get('stack_generation', 0)), int(image_source), device['id']))
   return 200, {'ok': True, 'role': 'template' if image_source else 'client',
                'client_enabled': not image_source}


def groups_for_device(device_id):
   with db() as conn:
      rows = conn.execute('SELECT group_name FROM device_groups WHERE device_id=? ORDER BY group_name', (device_id,)).fetchall()
   return [row['group_name'] for row in rows]


def capability_assignment_for_device(device_id, capability_id):
   groups = groups_for_device(device_id)
   with db() as conn:
      direct = conn.execute('''
         SELECT enabled, execution FROM capability_assignments
         WHERE capability_id=? AND target_type='device' AND target_id=?
      ''', (capability_id, device_id)).fetchone()
      if direct is not None:
         return dict(direct)

      device = conn.execute('SELECT template_device_id FROM devices WHERE id=?', (device_id,)).fetchone()
      if device and device['template_device_id']:
         template = conn.execute('''
            SELECT enabled, execution FROM capability_assignments
            WHERE capability_id=? AND target_type='template' AND target_id=?
         ''', (capability_id, device['template_device_id'])).fetchone()
         if template is not None:
            return dict(template)

      if groups:
         placeholders = ','.join('?' for _ in groups)
         rows = conn.execute(f'''
            SELECT enabled, execution FROM capability_assignments
            WHERE capability_id=? AND target_type='group' AND target_id IN ({placeholders})
         ''', [capability_id, *groups]).fetchall()
         if any(not bool(row['enabled']) for row in rows):
            return {'enabled': 0, 'execution': 'manual'}
         if any(bool(row['enabled']) for row in rows):
            selected = next(row for row in rows if bool(row['enabled']))
            return dict(selected)

      global_row = conn.execute('''
         SELECT enabled, execution FROM capability_assignments
         WHERE capability_id=? AND target_type='all' AND target_id='*'
      ''', (capability_id,)).fetchone()
      return dict(global_row) if global_row is not None else {'enabled': 0, 'execution': 'manual'}


def capability_enabled_for_device(device_id, capability_id):
   return bool(capability_assignment_for_device(device_id, capability_id)['enabled'])


def user_login(payload):
   username = str(payload.get('username', '')).strip()
   password = str(payload.get('password', ''))
   device_id = str(payload.get('device_id', '')).strip()
   if not username or not password or not device_id:
      return 400, {'error': 'missing credentials or device'}
   with db() as conn:
      device = conn.execute('SELECT id, is_image_source FROM devices WHERE id=?', (device_id,)).fetchone()
      if not device:
         return 403, {'error': 'device not registered'}
      if device['is_image_source']:
         return 403, {'error': 'user client disabled for template device'}
      user = conn.execute('SELECT full_name FROM users WHERE username=? AND enabled=1', (username,)).fetchone()
      session_token = secrets.token_urlsafe(32)
      now = now_ts()
      conn.execute('INSERT INTO sessions(token_hash, device_id, username, user_client_version, created_at, last_seen) VALUES(?,?,?,?,?,?)',
                   (token_hash(session_token), device_id, username, payload.get('user_client_version', ''), now, now))
      log_event(conn, device_id, username, 'user', 'login', '', {})
   return 200, {'session_token': session_token, 'username': username,
                'full_name': (user['full_name'] or '') if user else ''}


def authenticate_session(token):
   if not token:
      return None
   with db() as conn:
      row = conn.execute('SELECT * FROM sessions WHERE token_hash=?', (token_hash(token),)).fetchone()
   return row


def user_heartbeat(token):
   session = authenticate_session(token)
   if not session:
      return 401, {'error': 'unauthorized'}
   with db() as conn:
      conn.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now_ts(), session['token_hash']))
   return 200, {'ok': True}


def resolve_devices(target):
   with db() as conn:
      if target == 'all':
         return conn.execute('SELECT id, hostname FROM devices ORDER BY hostname').fetchall()
      if target.startswith('group:'):
         group_name = target.split(':', 1)[1]
         return conn.execute('''
            SELECT d.id, d.hostname FROM devices d
            JOIN device_groups g ON g.device_id=d.id
            WHERE g.group_name=? ORDER BY d.hostname
         ''', (group_name,)).fetchall()
      row = conn.execute('SELECT id, hostname FROM devices WHERE id=? OR hostname=?', (target, target)).fetchone()
      return [row] if row else []


def queue_action(device_id, capability_id, parameters=None, run_at=None, scope='system', username=''):
   now = now_ts()
   with db() as conn:
      device = conn.execute('SELECT id FROM devices WHERE id=? OR hostname=?', (device_id, device_id)).fetchone()
      if not device:
         raise ValueError('device not found: ' + device_id)
      cursor = conn.execute('''
         INSERT INTO actions(device_id, capability_id, parameters_json, run_at, status, created_at, scope, username)
         VALUES(?,?,?,?, 'queued', ?,?,?)
      ''', (device['id'], capability_id, json.dumps(parameters or {}, ensure_ascii=False),
            int(run_at or now), now, scope, username))
      return cursor.lastrowid


def poll_actions(device_id, token):
   device = authenticate_device(device_id, token)
   if not device:
      return 401, {'error': 'unauthorized'}
   now = now_ts()
   horizon = now + ACTION_PREFETCH
   lease = now + ACTION_LEASE
   with db() as conn:
      rows = conn.execute('''
         SELECT * FROM actions
         WHERE device_id=? AND scope='system' AND run_at<=? AND (
            status='queued' OR (status='running' AND COALESCE(lease_until,0)<?)
         ) ORDER BY run_at, id LIMIT 50
      ''', (device['id'], horizon, now)).fetchall()
      result = []
      for row in rows:
         if row['run_at'] <= now:
            conn.execute('UPDATE actions SET status="running", lease_until=?, started_at=COALESCE(started_at, ?) WHERE id=?',
                         (lease, now, row['id']))
         result.append({
            'id': row['id'],
            'capability_id': row['capability_id'],
            'parameters': json.loads(row['parameters_json'] or '{}'),
            'run_at': row['run_at'],
         })
   return 200, {'actions': result}


def action_result(device_id, token, payload):
   device = authenticate_device(device_id, token)
   if not device:
      return 401, {'error': 'unauthorized'}
   action_id = int(payload.get('action_id', 0))
   status = 'done' if payload.get('ok', True) else 'failed'
   result = payload.get('result', {})
   with db() as conn:
      row = conn.execute('SELECT * FROM actions WHERE id=? AND device_id=?', (action_id, device['id'])).fetchone()
      if not row:
         return 404, {'error': 'action not found'}
      conn.execute('UPDATE actions SET status=?, finished_at=?, lease_until=NULL, result_json=? WHERE id=?',
                   (status, now_ts(), json.dumps(result, ensure_ascii=False), action_id))
      log_event(conn, device['id'], '', 'system', 'action_result', row['capability_id'], {'action_id': action_id, 'status': status, 'result': result})
      if row['capability_id'] == '__lcs_reset_device__' and status == 'done':
         # Die Registrierung bleibt erhalten; nur die zurückgesetzte lokale
         # Geräteidentität darf den Server nicht mehr verwenden.
         conn.execute("UPDATE devices SET token_hash='' WHERE id=?", (device['id'],))
   return 200, {'ok': True}


def poll_user_actions(token):
   session = authenticate_session(token)
   if not session:
      return 401, {'error': 'unauthorized'}
   now = now_ts()
   with db() as conn:
      rows = conn.execute('''
         SELECT * FROM actions WHERE device_id=? AND scope='user' AND run_at<=? AND
            (username='' OR username=?) AND (status='queued' OR
            (status='running' AND COALESCE(lease_until,0)<?)) ORDER BY run_at,id LIMIT 20
      ''', (session['device_id'], now, session['username'], now)).fetchall()
      for row in rows:
         conn.execute('UPDATE actions SET status="running", lease_until=?, started_at=COALESCE(started_at,?) WHERE id=?',
                      (now + ACTION_LEASE, now, row['id']))
   return 200, {'actions': [{'id': row['id'], 'capability_id': row['capability_id'],
                             'parameters': json.loads(row['parameters_json'] or '{}')} for row in rows]}


def user_action_result(token, payload):
   session = authenticate_session(token)
   if not session:
      return 401, {'error': 'unauthorized'}
   with db() as conn:
      action_id = int(payload.get('action_id') or 0)
      if action_id:
         action = conn.execute("SELECT * FROM actions WHERE id=? AND device_id=? AND scope='user'",
                               (action_id, session['device_id'])).fetchone()
         if not action or (action['username'] and action['username'] != session['username']):
            return 404, {'error': 'action not found'}
         conn.execute('DELETE FROM actions WHERE id=?', (action_id,))
      log_event(conn, session['device_id'], session['username'], 'user', 'capability_result',
                str(payload.get('capability_id', '')), {'action_id': action_id,
                'ok': payload.get('ok', True), 'result': payload.get('result', {})})
   return 200, {'ok': True}


def log_event(conn, device_id, username, source, event_type, capability_id, payload):
   conn.execute('''
      INSERT INTO events(device_id, username, source, event_type, capability_id, payload_json, created_at)
      VALUES(?,?,?,?,?,?,?)
   ''', (device_id or None, username or None, source, event_type, capability_id or None,
         json.dumps(payload or {}, ensure_ascii=False), now_ts()))


def device_event(device_id, token, payload):
   device = authenticate_device(device_id, token)
   if not device:
      return 401, {'error': 'unauthorized'}
   with db() as conn:
      log_event(conn, device['id'], '', 'system', str(payload.get('event_type', 'event')),
                str(payload.get('capability_id', '')), payload.get('payload', {}))
   return 200, {'ok': True}


def delete_device_data(conn, device_id):
   conn.execute('DELETE FROM sessions WHERE device_id=?', (device_id,))
   conn.execute('DELETE FROM actions WHERE device_id=?', (device_id,))
   conn.execute('DELETE FROM events WHERE device_id=?', (device_id,))
   conn.execute('DELETE FROM device_groups WHERE device_id=?', (device_id,))
   conn.execute("DELETE FROM capability_assignments WHERE target_type='device' AND target_id=?", (device_id,))
   conn.execute('DELETE FROM devices WHERE id=?', (device_id,))


def self_delete(device_id, token):
   device = authenticate_device(device_id, token)
   if not device:
      return 401, {'error': 'unauthorized'}
   with db() as conn:
      delete_device_data(conn, device['id'])
   return 200, {'ok': True}
