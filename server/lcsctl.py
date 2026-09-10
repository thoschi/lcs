import argparse
import getpass
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / 'server.env'
if ENV_FILE.exists():
   for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
      line = raw.strip()
      if not line or line.startswith('#') or '=' not in line:
         continue
      key, value = line.split('=', 1)
      os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

from core import DB_PATH, SESSION_TTL, add_enrollment_token, delete_device_data, enrollment_settings, init_db, password_hash, queue_action, resolve_devices, token_hash

MANIFEST = Path(os.environ.get('LCS_MANIFEST_FILE', str(BASE / 'bootstrap-manifest.json')))
RELEASES = Path(os.environ.get('LCS_RELEASES_DIR', str(BASE / 'releases')))


def conn():
   c = sqlite3.connect(DB_PATH)
   c.row_factory = sqlite3.Row
   return c


def read_manifest():
   if MANIFEST.exists():
      return json.loads(MANIFEST.read_text(encoding='utf-8'))
   return {'generation': 0, 'capabilities': []}


def write_manifest(payload):
   MANIFEST.parent.mkdir(parents=True, exist_ok=True)
   tmp = MANIFEST.with_suffix('.tmp')
   tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
   os.replace(tmp, MANIFEST)


def bump_generation():
   payload = read_manifest()
   payload['generation'] = int(payload.get('generation', 0)) + 1
   write_manifest(payload)
   return payload['generation']


def resolve_device_id(db, value):
   row = db.execute('SELECT id, hostname FROM devices WHERE id=? OR hostname=?', (value, value)).fetchone()
   if not row:
      raise ValueError('Gerät nicht gefunden: ' + value)
   return row


def parse_target(db, target):
   if target == 'all':
      return 'all', '*', 'all'
   if target.startswith('group:'):
      name = target.split(':', 1)[1]
      row = db.execute('SELECT name FROM groups WHERE name=?', (name,)).fetchone()
      if not row:
         raise ValueError('Gruppe nicht gefunden: ' + name)
      return 'group', name, 'group:' + name
   if target.startswith('device:'):
      value = target.split(':', 1)[1]
   else:
      value = target
   row = resolve_device_id(db, value)
   return 'device', row['id'], 'device:' + row['hostname']


def cmd_init(args):
   init_db()
   print('Database:', DB_PATH)
   return 0


def cmd_user_add(args):
   password = args.password or getpass.getpass('Passwort: ')
   if not password:
      print('Leeres Passwort nicht erlaubt.', file=sys.stderr)
      return 2
   with conn() as db:
      db.execute('''
         INSERT INTO users(username, full_name, password_hash, enabled)
         VALUES(?,?,?,1)
         ON CONFLICT(username) DO UPDATE SET full_name=excluded.full_name, password_hash=excluded.password_hash, enabled=1
      ''', (args.username, args.full_name or '', password_hash(password)))
   print('Benutzer gespeichert:', args.username)
   return 0


def cmd_status(args):
   now = int(time.time())
   online_after = now - args.online_seconds
   session_after = now - SESSION_TTL
   with conn() as db:
      devices = db.execute('SELECT * FROM devices ORDER BY hostname').fetchall()
      sessions = db.execute('SELECT * FROM sessions WHERE last_seen>=? ORDER BY last_seen DESC', (session_after,)).fetchall()
      memberships = db.execute('SELECT * FROM device_groups ORDER BY group_name').fetchall()
   by_device = {}
   for session in sessions:
      by_device.setdefault(session['device_id'], []).append(session['username'])
   groups = {}
   for row in memberships:
      groups.setdefault(row['device_id'], []).append(row['group_name'])
   print(f"{'ID':16} {'HOSTNAME':22} {'STATUS':8} {'GRUPPEN':24} {'USER':18} {'STACK':5} LETZTER KONTAKT")
   print('-' * 120)
   for d in devices:
      online = d['last_seen'] >= online_after
      users = ','.join(by_device.get(d['id'], [])) or '-'
      group_text = ','.join(groups.get(d['id'], [])) or '-'
      stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['last_seen']))
      print(f"{d['id'][:16]:16} {d['hostname'][:22]:22} {('online' if online else 'offline'):8} {group_text[:24]:24} {users[:18]:18} {d['stack_generation']:5} {stamp}")
   if not devices:
      print('(noch keine Geräte registriert)')
   return 0


def cmd_devices(args):
   with conn() as db:
      rows = db.execute('SELECT * FROM devices ORDER BY hostname').fetchall()
   for row in rows:
      print(json.dumps(dict(row), ensure_ascii=False, indent=2))
   return 0


def cmd_group_add(args):
   with conn() as db:
      db.execute('INSERT INTO groups(name, description) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description',
                 (args.name, args.description or ''))
   print('Gruppe gespeichert:', args.name)
   return 0


def cmd_group_delete(args):
   with conn() as db:
      db.execute('DELETE FROM device_groups WHERE group_name=?', (args.name,))
      db.execute("DELETE FROM capability_assignments WHERE target_type='group' AND target_id=?", (args.name,))
      changed = db.execute('DELETE FROM groups WHERE name=?', (args.name,)).rowcount
   if not changed:
      print('Gruppe nicht gefunden:', args.name, file=sys.stderr)
      return 1
   print('Gruppe gelöscht:', args.name, 'Generation', bump_generation())
   return 0


def cmd_groups(args):
   with conn() as db:
      rows = db.execute('''
         SELECT g.name, g.description, COUNT(dg.device_id) AS devices
         FROM groups g LEFT JOIN device_groups dg ON dg.group_name=g.name
         GROUP BY g.name, g.description ORDER BY g.name
      ''').fetchall()
   for row in rows:
      print(f"{row['name']:24} {row['devices']:4}  {row['description']}")
   return 0


def cmd_group_add_device(args):
   with conn() as db:
      group = db.execute('SELECT name FROM groups WHERE name=?', (args.group,)).fetchone()
      if not group:
         raise ValueError('Gruppe nicht gefunden: ' + args.group)
      device = resolve_device_id(db, args.device)
      db.execute('INSERT OR IGNORE INTO device_groups(group_name, device_id) VALUES(?,?)', (args.group, device['id']))
   print(device['hostname'], '→', args.group, 'Generation', bump_generation())
   return 0


def cmd_group_remove_device(args):
   with conn() as db:
      device = resolve_device_id(db, args.device)
      db.execute('DELETE FROM device_groups WHERE group_name=? AND device_id=?', (args.group, device['id']))
   print(device['hostname'], 'aus', args.group, 'entfernt. Generation', bump_generation())
   return 0


def cmd_action(args):
   params = json.loads(args.json) if args.json else {}
   run_at = int(time.time())
   if args.at:
      run_at = int(time.mktime(time.strptime(args.at, '%Y-%m-%d %H:%M:%S')))
   devices = resolve_devices(args.target)
   if not devices:
      raise ValueError('Kein Gerät für Ziel gefunden: ' + args.target)
   ids = []
   for device in devices:
      ids.append(queue_action(device['id'], args.capability, params, run_at))
   print('%d Aktion(en) eingeplant: %s' % (len(ids), ', '.join(str(x) for x in ids)))
   return 0


def cmd_actions(args):
   sql = 'SELECT a.*, d.hostname FROM actions a JOIN devices d ON d.id=a.device_id'
   params = []
   if args.device:
      sql += ' WHERE d.id=? OR d.hostname=?'
      params = [args.device, args.device]
   sql += ' ORDER BY a.id DESC LIMIT ?'
   params.append(args.limit)
   with conn() as db:
      rows = db.execute(sql, params).fetchall()
   for row in rows:
      stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['run_at']))
      print(f"{row['id']:5} {row['hostname']:22} {row['status']:9} {stamp} {row['capability_id']}")
   return 0


def cmd_logs(args):
   with conn() as db:
      rows = db.execute('''
         SELECT e.*, d.hostname FROM events e LEFT JOIN devices d ON d.id=e.device_id
         ORDER BY e.id DESC LIMIT ?
      ''', (args.limit,)).fetchall()
   for row in rows:
      stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['created_at']))
      who = row['username'] or '-'
      print(f"{stamp} {(row['hostname'] or '-')[:22]:22} {row['source']:6} {who[:16]:16} {row['event_type']:20} {row['capability_id'] or '-'}")
   return 0


def validate_capability_manifest(data):
   for key in ('id', 'version', 'title', 'scope'):
      if not data.get(key):
         raise ValueError('manifest.json: %s fehlt' % key)
   if data['scope'] not in ('system', 'user'):
      raise ValueError('scope muss system oder user sein')
   if '/' in data['id'] or '\\' in data['id']:
      raise ValueError('ungültige Capability-ID')


def cmd_capability_publish(args):
   source = Path(args.directory).resolve()
   manifest_path = source / 'manifest.json'
   if not manifest_path.exists():
      raise ValueError('manifest.json fehlt in ' + str(source))
   cap = json.loads(manifest_path.read_text(encoding='utf-8'))
   validate_capability_manifest(cap)
   RELEASES.mkdir(parents=True, exist_ok=True)
   filename = '%s-%s.zip' % (cap['id'], cap['version'])
   archive = RELEASES / filename
   tmp = archive.with_suffix('.zip.tmp')
   with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
      for path in sorted(source.rglob('*')):
         if path.is_file() and '__pycache__' not in path.parts:
            zf.write(path, path.relative_to(source).as_posix())
   digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
   os.replace(tmp, archive)
   item = {
      'id': cap['id'],
      'version': cap['version'],
      'title': cap['title'],
      'description': cap.get('description', ''),
      'scope': cap['scope'],
      'tags': cap.get('tags', []),
      'triggers': cap.get('triggers', []),
      'filename': filename,
      'sha256': digest,
      'timeout': int(cap.get('timeout', 120)),
   }
   payload = read_manifest()
   caps = [x for x in payload.get('capabilities', []) if x.get('id') != cap['id']]
   caps.append(item)
   payload['capabilities'] = sorted(caps, key=lambda x: x['id'])
   payload['generation'] = int(payload.get('generation', 0)) + 1
   write_manifest(payload)
   print('%s %s veröffentlicht; noch keinem Client zugewiesen; Generation %s' % (cap['id'], cap['version'], payload['generation']))
   print('SHA256:', digest)
   return 0


def cmd_capability_remove(args):
   payload = read_manifest()
   old = len(payload.get('capabilities', []))
   payload['capabilities'] = [x for x in payload.get('capabilities', []) if x.get('id') != args.capability]
   if len(payload['capabilities']) == old:
      print('Capability nicht gefunden:', args.capability)
      return 1
   payload['generation'] = int(payload.get('generation', 0)) + 1
   write_manifest(payload)
   with conn() as db:
      db.execute('DELETE FROM capability_assignments WHERE capability_id=?', (args.capability,))
   print('Capability entfernt; Generation', payload['generation'])
   return 0


def cmd_capabilities(args):
   payload = read_manifest()
   with conn() as db:
      assignments = db.execute('SELECT * FROM capability_assignments ORDER BY capability_id, target_type, target_id').fetchall()
   by_cap = {}
   for row in assignments:
      sign = '+' if row['enabled'] else '-'
      target = 'all' if row['target_type'] == 'all' else row['target_type'] + ':' + row['target_id']
      by_cap.setdefault(row['capability_id'], []).append(sign + target)
   print('Generation:', payload.get('generation', 0))
   for cap in payload.get('capabilities', []):
      targets = ', '.join(by_cap.get(cap['id'], [])) or '(nicht zugewiesen)'
      print(f"{cap['id']:24} {cap['version']:10} {cap['scope']:6} {targets}")
   return 0


def cmd_capability_assign(args):
   payload = read_manifest()
   if not any(cap.get('id') == args.capability for cap in payload.get('capabilities', [])):
      raise ValueError('Capability nicht veröffentlicht: ' + args.capability)
   with conn() as db:
      target_type, target_id, label = parse_target(db, args.target)
      db.execute('''
         INSERT INTO capability_assignments(capability_id, target_type, target_id, enabled)
         VALUES(?,?,?,?)
         ON CONFLICT(capability_id, target_type, target_id) DO UPDATE SET enabled=excluded.enabled
      ''', (args.capability, target_type, target_id, 0 if args.disable else 1))
   print(args.capability, '→', label, 'deaktiviert' if args.disable else 'aktiviert', 'Generation', bump_generation())
   return 0


def cmd_capability_unassign(args):
   with conn() as db:
      target_type, target_id, label = parse_target(db, args.target)
      db.execute('DELETE FROM capability_assignments WHERE capability_id=? AND target_type=? AND target_id=?',
                 (args.capability, target_type, target_id))
   print('Zuweisung entfernt:', args.capability, label, 'Generation', bump_generation())
   return 0


def cmd_device_reset(args):
   with conn() as db:
      device = resolve_device_id(db, args.device)
      machine = db.execute('SELECT machine_id FROM devices WHERE id=?', (device['id'],)).fetchone()
      reenrollment_token = secrets.token_urlsafe(32)
      db.execute('''
         INSERT INTO enrollment_codes(machine_id, token_hash, expires_at, created_at)
         VALUES(?,?,?,?)
      ''', (machine['machine_id'], token_hash(reenrollment_token), int(time.time()) + 86400, int(time.time())))
   action_id = queue_action(device['id'], '__lcs_reset_device__', {'reenrollment_token': reenrollment_token}, int(time.time()))
   print('Reset an Client gesendet. Aktion:', action_id)
   print('Ein einmaliger Re-Enrollment-Code ist 24 Stunden gültig.')
   return 0


def cmd_device_delete(args):
   if not args.force:
      print('Für Online-Geräte bitte "device-reset" verwenden. Server-only Löschung benötigt --force.', file=sys.stderr)
      return 2
   with conn() as db:
      row = resolve_device_id(db, args.device)
      delete_device_data(db, row['id'])
   print('Serverdaten gelöscht:', row['hostname'], 'Generation', bump_generation())
   return 0


def cmd_token_create(args):
   settings = enrollment_settings(args.user_data, args.require_local_username, args.password_username)
   add_enrollment_token(args.name, args.password, not args.without_template, settings=settings)
   print('Vorläufiger Enrollment-Zugang erzeugt; der Installer ruft ihn mit dem Passwort ab.')
   return 0


def cmd_tokens(args):
   with conn() as db:
      rows = db.execute('SELECT * FROM enrollment_tokens ORDER BY created_at DESC').fetchall()
   for row in rows:
      status = 'aktiv' if row['enabled'] else 'widerrufen'
      kind = 'Vorlage' if row['token_type'] == 'template' else 'einmalig'
      template = row['template_device_id'] or 'vorläufig'
      last_used = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['last_used_at'])) if row['last_used_at'] else '-'
      print(f"{row['id']:4} {row['name'][:28]:28} {kind:9} {template[:16]:16} {row['token_prefix']}… {status:10} {row['enrollment_count']:5} {last_used}")
   return 0


def cmd_token_revoke(args):
   with conn() as db:
      changed = db.execute('UPDATE enrollment_tokens SET enabled=0 WHERE id=? OR name=?',
                           (args.token, args.token)).rowcount
   if not changed:
      raise ValueError('Enrollment-Token nicht gefunden: ' + args.token)
   print('Enrollment-Token widerrufen:', args.token)
   return 0


def main():
   parser = argparse.ArgumentParser(prog='lcsctl')
   sub = parser.add_subparsers(dest='command', required=True)

   p = sub.add_parser('init'); p.set_defaults(func=cmd_init)
   p = sub.add_parser('user-add'); p.add_argument('username'); p.add_argument('--full-name', default=''); p.add_argument('--password', default=''); p.set_defaults(func=cmd_user_add)
   p = sub.add_parser('status'); p.add_argument('--online-seconds', type=int, default=60); p.set_defaults(func=cmd_status)
   p = sub.add_parser('devices'); p.set_defaults(func=cmd_devices)
   p = sub.add_parser('group-add'); p.add_argument('name'); p.add_argument('--description', default=''); p.set_defaults(func=cmd_group_add)
   p = sub.add_parser('group-delete'); p.add_argument('name'); p.set_defaults(func=cmd_group_delete)
   p = sub.add_parser('groups'); p.set_defaults(func=cmd_groups)
   p = sub.add_parser('group-add-device'); p.add_argument('group'); p.add_argument('device'); p.set_defaults(func=cmd_group_add_device)
   p = sub.add_parser('group-remove-device'); p.add_argument('group'); p.add_argument('device'); p.set_defaults(func=cmd_group_remove_device)
   p = sub.add_parser('action'); p.add_argument('target', help='hostname/device-id, group:NAME oder all'); p.add_argument('capability'); p.add_argument('--json', default='{}'); p.add_argument('--at', help='YYYY-MM-DD HH:MM:SS'); p.set_defaults(func=cmd_action)
   p = sub.add_parser('actions'); p.add_argument('--device'); p.add_argument('--limit', type=int, default=30); p.set_defaults(func=cmd_actions)
   p = sub.add_parser('logs'); p.add_argument('--limit', type=int, default=50); p.set_defaults(func=cmd_logs)
   p = sub.add_parser('capability-publish'); p.add_argument('directory'); p.set_defaults(func=cmd_capability_publish)
   p = sub.add_parser('capability-remove'); p.add_argument('capability'); p.set_defaults(func=cmd_capability_remove)
   p = sub.add_parser('capabilities'); p.set_defaults(func=cmd_capabilities)
   p = sub.add_parser('capability-assign'); p.add_argument('capability'); p.add_argument('target', help='all, group:NAME oder device:HOST'); p.add_argument('--disable', action='store_true'); p.set_defaults(func=cmd_capability_assign)
   p = sub.add_parser('capability-unassign'); p.add_argument('capability'); p.add_argument('target'); p.set_defaults(func=cmd_capability_unassign)
   p = sub.add_parser('device-reset'); p.add_argument('device'); p.set_defaults(func=cmd_device_reset)
   p = sub.add_parser('device-delete'); p.add_argument('device'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_device_delete)
   p = sub.add_parser('token-create'); p.add_argument('name'); p.add_argument('password'); p.add_argument('--without-template', action='store_true'); p.add_argument('--user-data', default=''); p.add_argument('--password-username', default=''); p.add_argument('--require-local-username', action='store_true'); p.set_defaults(func=cmd_token_create)
   p = sub.add_parser('tokens'); p.set_defaults(func=cmd_tokens)
   p = sub.add_parser('token-revoke'); p.add_argument('token', help='ID oder Name'); p.set_defaults(func=cmd_token_revoke)

   args = parser.parse_args()
   init_db()
   try:
      return args.func(args)
   except ValueError as exc:
      print(str(exc), file=sys.stderr)
      return 2


if __name__ == '__main__':
   raise SystemExit(main())
