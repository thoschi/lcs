import json
import os
import secrets
import time
import tarfile
import hashlib
import io
import zipfile
from functools import wraps
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import core

HOST = os.environ.get('LCS_SERVER_HOST', '127.0.0.1')
PORT = int(os.environ.get('LCS_SERVER_PORT', '5000'))
BASE = Path(__file__).resolve().parent
RELEASES = Path(os.environ.get('LCS_RELEASES_DIR', str(BASE / 'releases')))
MANIFEST = Path(os.environ.get('LCS_MANIFEST_FILE', str(BASE / 'data/bootstrap-manifest.json')))
MAX_REQUEST_BYTES = int(os.environ.get('LCS_MAX_REQUEST_BYTES', str(2 * 1024 * 1024)))
SOURCE_ROOT = Path(os.environ.get('LCS_SOURCE_ROOT', '/opt/lcs'))
ADMIN_USERS = {value.strip() for value in os.environ.get('LCS_ADMIN_USERS', '').split(',') if value.strip()}

core.init_db()

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.config.update(
   SECRET_KEY=os.environ.get('LCS_SECRET_KEY') or secrets.token_hex(32),
   MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
oauth = OAuth(app)

OIDC_DISCOVERY_URL = os.environ.get('LCS_OIDC_DISCOVERY_URL', '').strip()
if OIDC_DISCOVERY_URL:
   oauth.register(
      name='keycloak',
      client_id=os.environ.get('LCS_OIDC_CLIENT_ID', ''),
      client_secret=os.environ.get('LCS_OIDC_CLIENT_SECRET', ''),
      server_metadata_url=OIDC_DISCOVERY_URL,
      client_kwargs={'scope': 'openid profile email'},
   )


@app.template_filter('datetime')
def format_datetime(value):
   if not value:
      return '–'
   return time.strftime('%d.%m.%Y %H:%M', time.localtime(int(value)))


@app.template_filter('jsonpretty')
def format_json(value):
   if not value:
      return 'Keine Rückmeldung'
   try:
      value = json.loads(value) if isinstance(value, str) else value
   except json.JSONDecodeError:
      pass
   return json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value


def bearer():
   value = request.headers.get('Authorization', '')
   return value[7:] if value.startswith('Bearer ') else ''


def load_manifest():
   if not MANIFEST.exists():
      return {'generation': 0, 'capabilities': []}
   return json.loads(MANIFEST.read_text(encoding='utf-8'))


def load_capability_for_editor(capability_id):
   cap = next((item for item in load_manifest().get('capabilities', [])
               if item.get('id') == capability_id), None)
   if not cap:
      raise ValueError('Aktion nicht gefunden')
   archive = RELEASES / str(cap.get('filename', ''))
   if not archive.is_file() or archive.parent != RELEASES:
      raise ValueError('Aktionspaket nicht gefunden')
   with zipfile.ZipFile(archive) as package:
      try:
         packaged_manifest = json.loads(package.read('manifest.json').decode('utf-8'))
         entrypoint = str(packaged_manifest.get('entrypoint', 'action.py'))
         code = package.read(entrypoint).decode('utf-8')
      except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
         raise ValueError('Aktionspaket kann nicht im Editor geöffnet werden') from exc
   packaged_manifest.update(cap)
   packaged_manifest['parameter_example'] = packaged_manifest.get('parameter_example') or {}
   packaged_manifest['code'] = code
   return packaged_manifest


def write_manifest(payload):
   tmp = MANIFEST.with_suffix('.tmp')
   tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
   os.replace(tmp, MANIFEST)


def publish_capability(source):
   manifest_file = source / 'manifest.json'
   cap = json.loads(manifest_file.read_text(encoding='utf-8'))
   capability_id = str(cap.get('id', ''))
   if not capability_id or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for char in capability_id):
      raise ValueError('Ungültige Capability-ID')
   if cap.get('scope') not in ('system', 'user'):
      raise ValueError('Scope muss system oder user sein')
   RELEASES.mkdir(parents=True, exist_ok=True)
   filename = '%s-%s.zip' % (capability_id, cap['version'])
   archive = RELEASES / filename
   with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
      for path in sorted(source.rglob('*')):
         if path.is_file() and '__pycache__' not in path.parts:
            package.write(path, path.relative_to(source).as_posix())
   item = {key: cap.get(key) for key in ('id', 'version', 'title', 'description', 'scope',
           'tags', 'triggers', 'timeout', 'requires_password', 'conditions', 'on_login_credentials',
           'user_executable', 'parameter_example') if cap.get(key) is not None}
   item.update(filename=filename, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
   payload = load_manifest()
   payload['capabilities'] = [entry for entry in payload.get('capabilities', []) if entry.get('id') != capability_id]
   payload['capabilities'].append(item)
   payload['capabilities'].sort(key=lambda entry: entry['id'])
   bump_generation(payload)


def bump_generation(payload=None):
   payload = payload or load_manifest()
   payload['generation'] = int(payload.get('generation', 0)) + 1
   write_manifest(payload)


def load_manifest_for_device(device):
   payload = load_manifest()
   selected = []
   trigger_map = {
      'startup': [{'type': 'startup'}],
      'hourly': [{'type': 'interval', 'seconds': 3600}],
      'daily': [{'type': 'daily', 'at': '00:00'}],
   }
   for capability in payload.get('capabilities', []):
      assignment = core.capability_assignment_for_device(device['id'], capability['id'])
      if not assignment['enabled']:
         continue
      capability = dict(capability)
      capability['triggers'] = trigger_map.get(assignment.get('execution'), [])
      selected.append(capability)
   return {'generation': payload.get('generation', 0), 'capabilities': selected}


def device():
   return core.authenticate_device(request.headers.get('X-Device-ID', ''), bearer())


def api_result(result):
   status, payload = result
   return jsonify(payload), status


def current_admin():
   user = session.get('admin')
   return user if user and user.get('username') in ADMIN_USERS else None


def admin_required(func):
   @wraps(func)
   def wrapped(*args, **kwargs):
      if not current_admin():
         return redirect(url_for('login', next=request.full_path))
      return func(*args, **kwargs)
   return wrapped


def check_csrf():
   if not secrets.compare_digest(session.get('csrf', ''), request.form.get('csrf', '')):
      abort(400, 'Ungültiges Formular-Token')


def dashboard_data():
   now = core.now_ts()
   with core.db() as conn:
      devices = [dict(row) for row in conn.execute('''
         SELECT d.*, GROUP_CONCAT(dg.group_name, ', ') AS groups
         FROM devices d LEFT JOIN device_groups dg ON dg.device_id=d.id
         GROUP BY d.id ORDER BY d.hostname
      ''').fetchall()]
      groups = [dict(row) for row in conn.execute('''
         SELECT g.*, COUNT(DISTINCT dg.device_id) AS device_count,
            COUNT(DISTINCT at.id) AS preset_count
         FROM groups g LEFT JOIN device_groups dg ON dg.group_name=g.name
         LEFT JOIN action_templates at ON at.group_name=g.name
         GROUP BY g.name ORDER BY g.name
      ''').fetchall()]
      presets = conn.execute('SELECT id, group_name, capability_id FROM action_templates ORDER BY id').fetchall()
      for group in groups:
         group['presets'] = [dict(row) for row in presets if row['group_name'] == group['name']]
      assignments = [dict(row) for row in conn.execute(
         'SELECT * FROM capability_assignments ORDER BY capability_id, target_type, target_id').fetchall()]
      tokens = [dict(row) for row in conn.execute('''
         SELECT et.*, d.hostname AS template_hostname
         FROM enrollment_tokens et LEFT JOIN devices d ON d.id=et.template_device_id
         ORDER BY et.created_at DESC
      ''').fetchall()]
      actions = [dict(row) for row in conn.execute('''
         SELECT a.*, d.hostname, executed.platform AS execution_platform,
            executed.id AS executed_device_id
         FROM actions a JOIN devices d ON d.id=a.device_id
         LEFT JOIN devices executed ON executed.id=a.execution_device_id
         ORDER BY a.id DESC LIMIT 40
      ''').fetchall()]
   for item in devices:
      item['online'] = item['last_seen'] >= now - 60
      try:
         item['hardware'] = json.loads(item.get('hardware_json') or '{}')
      except json.JSONDecodeError:
         item['hardware'] = {}
      labels = {
         'ip_addresses': 'IP-Adressen', 'mac_addresses': 'MAC-Adressen',
         'serial_number': 'Seriennummer', 'operating_system': 'Betriebssystem',
         'os_release': 'Systemversion', 'architecture': 'Architektur',
         'processor': 'Prozessor', 'manufacturer': 'Hersteller', 'model': 'Modell',
         'bios': 'BIOS', 'memory_bytes': 'Arbeitsspeicher (Bytes)',
         'software': 'Installierte Software',
      }
      item['info_items'] = [
         {'label': labels.get(key, key.replace('_', ' ').title()), 'value': value}
         for key, value in item['hardware'].items()
      ]
   platform_labels = {'windows': 'Win', 'linux': 'Lin', 'linbo': 'Lbo'}
   for item in devices:
      same_host = [device for device in devices
                   if device['hostname'].lower() == item['hostname'].lower() and device.get('platform')]
      current = max(same_host, key=lambda device: device['last_seen'], default=item)
      platforms = []
      for device in same_host:
         platform = device['platform'].lower()
         if platform not in [entry['value'] for entry in platforms]:
            platforms.append({'value': platform, 'label': platform_labels.get(platform, device['platform']),
                              'current': platform == (current.get('platform') or '').lower()})
      item['platforms'] = platforms
      item['platform_filter'] = ' '.join(entry['value'] for entry in platforms)
   template_tree = []
   for template in (item for item in devices if item['is_image_source']):
      branch = dict(template)
      branch['clients'] = [item for item in devices if item.get('template_device_id') == template['id'] and item['id'] != template['id']]
      template_tree.append(branch)
   return devices, groups, assignments, tokens, actions, template_tree


def audit_data():
   with core.db() as conn:
      audits = [dict(row) for row in conn.execute('''
         SELECT * FROM device_audit_log ORDER BY created_at DESC, id DESC
      ''').fetchall()]
      action_rows = [dict(row) for row in conn.execute('''
         SELECT a.*, COALESCE(d.hostname,
            (SELECT MAX(l.hostname) FROM device_audit_log l WHERE l.device_id=a.device_id),
            a.device_id) AS hostname
         FROM actions a LEFT JOIN devices d ON d.id=a.device_id
      ''').fetchall()]
   labels = {'registered': 'Registrierung', 'reregistered': 'Neuregistrierung',
             'token_changed': 'Token geändert'}
   entries = [{
      **item, 'key': 'audit-%s' % item['id'],
      'aspect': 'token' if item['event_type'] == 'token_changed' else 'registration',
      'action': labels.get(item['event_type'], item['event_type']),
      'status': '', 'result_json': '', 'timestamp': item['created_at'],
   } for item in audits]
   entries.extend({
      **item, 'key': 'action-%s' % item['id'], 'aspect': 'action',
      'action': item['capability_id'], 'event_type': 'action',
      'old_token_hash': '', 'new_token_hash': '', 'timestamp': item['created_at'],
   } for item in action_rows)
   entries.sort(key=lambda item: (item['timestamp'], item['key']), reverse=True)
   return entries


def render_admin(new_token=None, editor=None, page='overview'):
   devices, groups, assignments, tokens, actions, template_tree = dashboard_data()
   task_devices = []
   for item in (device for device in devices if not device['is_image_source']):
      existing = next((device for device in task_devices
                       if device['hostname'].lower() == item['hostname'].lower()), None)
      if existing:
         existing['connection_count'] += 1
         existing['connections'].append(item)
      else:
         target = dict(item)
         target['connection_count'] = 1
         target['connections'] = [item]
         task_devices.append(target)
   logs = audit_data() if page == 'logging' else []
   device_by_id = {item['id']: item for item in devices}
   for token in tokens:
      token['template'] = device_by_id.get(token['template_device_id'])
      token['clients'] = [item for item in devices if
         (token['token_type'] == 'template' and token['template_device_id'] and
          item.get('template_device_id') == token['template_device_id'] and
          item['id'] != token['template_device_id']) or
         (token['token_type'] != 'template' and token['group_name'] in (item.get('groups') or '').split(', '))]
   manifest = load_manifest()
   generation = int(manifest.get('generation', 0))
   for capability in manifest.get('capabilities', []):
      installed, pending = [], []
      for assignment in (item for item in assignments if item['capability_id'] == capability['id'] and item['enabled']):
         targets = devices if assignment['target_type'] == 'all' else (
            [device_by_id[assignment['target_id']]] if assignment['target_type'] in ('device', 'template') and assignment['target_id'] in device_by_id else
            [item for item in devices if assignment['target_type'] == 'group' and assignment['target_id'] in (item.get('groups') or '').split(', ')])
         for target in targets:
            bucket = installed if int(target.get('stack_generation') or 0) >= generation else pending
            if target not in bucket:
               bucket.append(target)
      capability['installed_clients'], capability['pending_clients'] = installed, pending
   for device in devices:
      device['capability_states'] = []
      device['executable_capabilities'] = []
      for capability in manifest.get('capabilities', []):
         assigned = core.capability_enabled_for_device(device['id'], capability['id'])
         installed = device in capability['installed_clients']
         device['capability_states'].append({
            'id': capability['id'], 'title': capability['title'],
            'assigned': assigned, 'installed': assigned and installed,
         })
         if assigned and capability.get('scope', 'system') == 'system':
            device['executable_capabilities'].append({
               'id': capability['id'], 'title': capability['title'],
               'parameters': capability.get('parameter_example') or {},
            })
      device['pending_task_count'] = sum(state['assigned'] and not state['installed']
                                         for state in device['capability_states'])
   for group in groups:
      members = [device for device in devices if group['name'] in (device.get('groups') or '').split(', ')]
      group['members'] = members
      group['capability_states'] = []
      for capability in manifest.get('capabilities', []):
         assignment = next((item for item in assignments
            if item['capability_id'] == capability['id'] and item['target_type'] == 'group'
            and item['target_id'] == group['name']), None)
         assigned = bool(assignment and assignment['enabled'])
         installed_count = sum(device in capability['installed_clients'] for device in members) if assigned else 0
         group['capability_states'].append({
            'id': capability['id'], 'title': capability['title'], 'assigned': assigned,
            'installed_count': installed_count, 'member_count': len(members),
         })
   all_group = {'name': 'alle', 'description': 'Alle Clients', 'device_count': len(devices),
                'members': devices, 'virtual': True, 'capability_states': []}
   for capability in manifest.get('capabilities', []):
      assignment = next((item for item in assignments
         if item['capability_id'] == capability['id'] and item['target_type'] == 'all'), None)
      assigned = bool(assignment and assignment['enabled'])
      all_group['capability_states'].append({
         'id': capability['id'], 'title': capability['title'], 'assigned': assigned,
         'installed_count': len(capability['installed_clients']) if assigned else 0,
         'member_count': len(devices),
      })
   return render_template('admin.html', devices=devices, groups=groups, assignments=assignments,
                          tokens=tokens, actions=actions, manifest=manifest,
                          now=core.now_ts(), new_token=new_token, editor=editor or {}, template_tree=template_tree,
                          task_devices=task_devices, page=page, logs=logs, all_group=all_group)


@app.get('/health')
def health():
   return jsonify(ok=True, version='0.6')


@app.get('/api/v1/bootstrap/manifest')
def bootstrap_manifest():
   authenticated = device()
   if not authenticated:
      return jsonify(error='unauthorized'), 401
   return jsonify(load_manifest_for_device(authenticated))


@app.get('/api/v1/bootstrap/package/<path:filename>')
def bootstrap_package(filename):
   authenticated = device()
   if not authenticated:
      return jsonify(error='unauthorized'), 401
   if '/' in filename or '\\' in filename or filename.startswith('.'):
      return jsonify(error='invalid filename'), 400
   allowed = {cap.get('filename') for cap in load_manifest_for_device(authenticated).get('capabilities', [])}
   if filename not in allowed:
      return jsonify(error='package not assigned to device'), 403
   target = RELEASES / filename
   if not target.is_file():
      return jsonify(error='package not found'), 404
   return send_file(target, mimetype='application/zip', conditional=True)


@app.get('/api/v1/update/source')
def update_source():
   if not device():
      return jsonify(error='unauthorized'), 401
   if not (SOURCE_ROOT / 'install.sh').is_file():
      return jsonify(error='server source tree unavailable'), 503
   archive = RELEASES / 'lcs-source.tar.gz'
   with tarfile.open(archive, 'w:gz') as output:
      for name in ('install.sh', 'install.ps1', 'VERSION', 'server', 'system', 'client'):
         path = SOURCE_ROOT / name
         if path.exists():
            output.add(path, arcname=name, filter=lambda item: None if '__pycache__' in item.name else item)
   return send_file(archive, mimetype='application/gzip', conditional=True)


@app.get('/api/v1/agent/poll')
def agent_poll():
   return api_result(core.poll_actions(request.headers.get('X-Device-ID', ''), bearer()))


@app.get('/api/v1/user/poll')
def user_poll():
   return api_result(core.poll_user_actions(bearer()))


@app.post('/api/v1/<path:endpoint>')
def agent_api(endpoint):
   payload = request.get_json(silent=False) or {}
   device_id = request.headers.get('X-Device-ID', '')
   routes = {
      'enroll': lambda: core.enroll(payload),
      'token/claim': lambda: core.claim_enrollment_token(payload.get('hostname', ''), payload.get('password', '')),
      'token/check': lambda: core.check_enrollment_token(payload.get('token_hash', ''), payload.get('hostname', '')),
      'heartbeat': lambda: core.heartbeat(device_id, bearer(), payload),
      'action/result': lambda: core.action_result(device_id, bearer(), payload),
      'event': lambda: core.device_event(device_id, bearer(), payload),
      'device/self-delete': lambda: core.self_delete(device_id, bearer()),
      'user/login': lambda: core.user_login(payload),
      'user/heartbeat': lambda: core.user_heartbeat(bearer()),
      'user/action/result': lambda: core.user_action_result(bearer(), payload),
   }
   if endpoint not in routes:
      return jsonify(error='not found'), 404
   return api_result(routes[endpoint]())


@app.get('/')
def index():
   return redirect(url_for('admin'))


@app.get('/login')
def login():
   if not OIDC_DISCOVERY_URL:
      return render_template('setup.html'), 503
   session['login_next'] = request.args.get('next', url_for('admin'))
   return oauth.keycloak.authorize_redirect(url_for('auth_callback', _external=True))


@app.get('/auth/callback')
def auth_callback():
   token = oauth.keycloak.authorize_access_token()
   claims = token.get('userinfo') or oauth.keycloak.get('userinfo').json()
   username = claims.get('preferred_username') or claims.get('email') or claims.get('sub')
   if username not in ADMIN_USERS:
      session.clear()
      return render_template('denied.html', username=username), 403
   session['admin'] = {'username': username, 'name': claims.get('name') or username}
   session['csrf'] = secrets.token_urlsafe(24)
   return redirect(session.pop('login_next', url_for('admin')))


@app.get('/logout')
def logout():
   session.clear()
   return redirect(url_for('login'))


@app.get('/admin')
@admin_required
def admin():
   return render_admin()


@app.get('/admin/clients')
@admin_required
def admin_clients():
   return render_admin(page='clients')


@app.get('/admin/tasks')
@admin_required
def admin_tasks():
   capability_id = request.args.get('edit', '').strip()
   if not capability_id:
      return render_admin(page='tasks')
   try:
      editor = load_capability_for_editor(capability_id)
   except (ValueError, zipfile.BadZipFile) as exc:
      flash(str(exc), 'error')
      return redirect(url_for('admin_tasks') + '#capabilities')
   return render_admin(editor=editor, page='tasks')


@app.get('/admin/tokens')
@admin_required
def admin_tokens():
   return render_admin(page='tokens')


@app.get('/admin/logging')
@admin_required
def admin_logging():
   return render_admin(page='logging')


@app.get('/admin/client-status')
@admin_required
def client_status():
   devices, _, _, _, actions, _ = dashboard_data()
   manifest = load_manifest()
   generation = int(manifest.get('generation', 0))
   for device in devices:
      states = []
      executable = []
      for capability in manifest.get('capabilities', []):
         assigned = core.capability_enabled_for_device(device['id'], capability['id'])
         installed = assigned and int(device.get('stack_generation') or 0) >= generation
         states.append({'id': capability['id'], 'title': capability['title'],
                        'assigned': assigned, 'installed': installed})
         if assigned and capability.get('scope', 'system') == 'system':
            executable.append({'id': capability['id'], 'title': capability['title'],
                               'parameters': capability.get('parameter_example') or {}})
      device['capability_states'] = states
      device['executable_capabilities'] = executable
   return jsonify(devices=[{
      'id': item['id'],
      'online': item['online'],
      'last_seen': item['last_seen'],
      'last_seen_text': format_datetime(item['last_seen']),
      'hostname': item['hostname'],
      'platform': item['platform'] or '',
      'platforms': item['platforms'],
      'platform_filter': item['platform_filter'],
      'agent_version': item['agent_version'] or '',
      'groups': item['groups'] or '',
      'hardware': item['hardware'],
      'is_image_source': bool(item.get('is_image_source')),
      'capability_states': item['capability_states'],
      'executable_capabilities': item['executable_capabilities'],
      'pending_task_count': sum(state['assigned'] and not state['installed']
                                for state in item['capability_states']),
   } for item in devices], actions=[{
      'id': item['id'],
      'hostname': item['hostname'],
      'capability_id': item['capability_id'],
      'status': item['status'],
      'run_at': item['run_at'],
      'finished_at': item['finished_at'],
      'result': json.loads(item['result_json']) if item['result_json'] else None,
      'execution_device_id': item['executed_device_id'] or '',
      'execution_platform': item['execution_platform'] or '',
   } for item in actions], now=core.now_ts())


@app.post('/admin/group')
@admin_required
def save_group():
   check_csrf()
   name = request.form.get('name', '').strip()
   original_name = request.form.get('original_name', '').strip()
   if not name or name.lower() == 'alle':
      abort(400, 'Ungültiger Gruppenname')
   with core.db() as conn:
      if original_name and original_name != name:
         if conn.execute('SELECT 1 FROM groups WHERE name=?', (name,)).fetchone():
            abort(409, 'Der Gruppenname ist bereits vergeben')
         conn.execute('UPDATE groups SET name=? WHERE name=?', (name, original_name))
         conn.execute('UPDATE device_groups SET group_name=? WHERE group_name=?', (name, original_name))
         conn.execute('UPDATE action_templates SET group_name=? WHERE group_name=?', (name, original_name))
         conn.execute("UPDATE capability_assignments SET target_id=? WHERE target_type='group' AND target_id=?", (name, original_name))
      conn.execute('''INSERT INTO groups(name, description) VALUES(?,?)
         ON CONFLICT(name) DO UPDATE SET description=excluded.description''',
         (name, request.form.get('description', '').strip()))
      if original_name:
         selected = set(request.form.getlist('device_ids'))
         conn.execute('DELETE FROM device_groups WHERE group_name=?', (name,))
         conn.executemany('INSERT INTO device_groups(group_name, device_id) VALUES(?,?)',
                          [(name, device_id) for device_id in selected])
   if original_name:
      bump_generation()
   flash('Gruppe gespeichert.', 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/group-membership')
@admin_required
def group_membership():
   check_csrf()
   group = request.form.get('group', '')
   device_id = request.form.get('device_id', '')
   with core.db() as conn:
      if request.form.get('operation') == 'remove':
         conn.execute('DELETE FROM device_groups WHERE group_name=? AND device_id=?', (group, device_id))
      else:
         conn.execute('INSERT OR IGNORE INTO device_groups(group_name, device_id) VALUES(?,?)', (group, device_id))
   bump_generation()
   flash('Gruppenzuordnung aktualisiert.', 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/device/<device_id>/generalize')
@admin_required
def generalize_device(device_id):
   check_csrf()
   try:
      reenrollment_token = core.create_reenrollment_token(device_id)
      core.queue_action(device_id, '__lcs_reset_device__',
                        {'reenrollment_token': reenrollment_token}, core.now_ts())
   except ValueError as exc:
      flash(str(exc), 'error')
   else:
      flash('Generalisierung eingeplant. Ein neuer einmaliger Token wird an den Client ausgeliefert.', 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/device/<device_id>/delete')
@admin_required
def delete_device(device_id):
   check_csrf()
   with core.db() as conn:
      device_row = conn.execute('SELECT hostname FROM devices WHERE id=?', (device_id,)).fetchone()
      if not device_row:
         abort(404)
      core.delete_device_data(conn, device_id)
   flash('Serverdaten für %s vollständig gelöscht.' % device_row['hostname'], 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/group/<name>/delete')
@admin_required
def delete_group(name):
   check_csrf()
   with core.db() as conn:
      conn.execute('DELETE FROM device_groups WHERE group_name=?', (name,))
      conn.execute('DELETE FROM action_templates WHERE group_name=?', (name,))
      conn.execute("DELETE FROM capability_assignments WHERE target_type='group' AND target_id=?", (name,))
      conn.execute('DELETE FROM groups WHERE name=?', (name,))
   bump_generation()
   flash('Gruppe gelöscht.', 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/action-template/<int:template_id>/delete')
@admin_required
def delete_action_template(template_id):
   check_csrf()
   with core.db() as conn:
      conn.execute('DELETE FROM action_templates WHERE id=?', (template_id,))
   flash('Vorbereitete Aufgabe entfernt.', 'success')
   return redirect(url_for('admin_clients') + '#devices')


@app.post('/admin/token')
@admin_required
def create_token():
   check_csrf()
   try:
      settings = core.enrollment_settings(
         request.form.get('user_data', ''), request.form.get('use_domain_username') == '1',
         request.form.get('password_username', ''))
      token = core.add_enrollment_token(request.form.get('name', ''), request.form.get('password', ''),
                                        settings=settings, hostname=request.form.get('hostname', ''),
                                        token_type=request.form.get('token_type', 'template'))
   except Exception as exc:
      flash(str(exc), 'error')
      return redirect(url_for('admin_tokens') + '#tokens')
   flash('Vorläufiger Zugang erzeugt. Er wird beim ersten Enrollment aktiviert.', 'success')
   return render_admin(new_token=token, page='tokens')


@app.post('/admin/token/<int:token_id>/toggle')
@admin_required
def toggle_token(token_id):
   check_csrf()
   with core.db() as conn:
      conn.execute('UPDATE enrollment_tokens SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?',
                   (token_id,))
   flash('Token-Status geändert.', 'success')
   return redirect(url_for('admin_tokens') + '#tokens')


@app.post('/admin/token/<int:token_id>/copy')
@admin_required
def copy_token(token_id):
   check_csrf()
   with core.db() as conn:
      token = conn.execute('SELECT token_value FROM enrollment_tokens WHERE id=?', (token_id,)).fetchone()
   if not token:
      abort(404)
   if not token['token_value']:
      abort(409, 'Dieser ältere Token muss einmal über den Installer abgerufen oder neu erstellt werden')
   return jsonify(token=token['token_value'])


@app.get('/admin/token/<int:token_id>/download')
@admin_required
def download_token(token_id):
   with core.db() as conn:
      token = conn.execute('SELECT name, token_value FROM enrollment_tokens WHERE id=?', (token_id,)).fetchone()
   if not token:
      abort(404)
   if not token['token_value']:
      abort(409, 'Für diesen älteren Token ist keine Token-Datei verfügbar')
   safe_name = ''.join(char if char.isalnum() or char in '-_' else '-' for char in token['name']).strip('-') or 'enrollment'
   return send_file(io.BytesIO((token['token_value'] + '\n').encode()), mimetype='text/plain',
                    as_attachment=True, download_name=safe_name + '.token')


@app.post('/admin/token/<int:token_id>/delete')
@admin_required
def delete_token(token_id):
   check_csrf()
   with core.db() as conn:
      token = conn.execute('SELECT name FROM enrollment_tokens WHERE id=?', (token_id,)).fetchone()
      if not token:
         abort(404)
      conn.execute('DELETE FROM enrollment_tokens WHERE id=?', (token_id,))
   flash('Enrollment-Token %s gelöscht.' % token['name'], 'success')
   return redirect(url_for('admin_tokens') + '#tokens')


@app.post('/admin/capability-editor')
@admin_required
def capability_editor():
   check_csrf()
   try:
      capability_id = request.form.get('id', '').strip().lower()
      if not capability_id or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for char in capability_id):
         raise ValueError('Ungültige Capability-ID')
      version = request.form.get('version', '1.0.0').strip()
      source = RELEASES / 'editor' / capability_id
      source.mkdir(parents=True, exist_ok=True)
      try:
         manifest = load_capability_for_editor(capability_id)
      except (ValueError, zipfile.BadZipFile):
         manifest = {}
      for generated_key in ('code', 'filename', 'sha256'):
         manifest.pop(generated_key, None)
      manifest.update({
         'id': capability_id, 'version': version,
         'title': request.form.get('title', '').strip() or capability_id,
         'description': request.form.get('description', '').strip(),
         'scope': request.form.get('scope', 'system'),
         'user_executable': request.form.get('user_executable') == '1',
         'timeout': max(1, int(request.form.get('timeout', '120'))),
         'entrypoint': 'action.py',
         'parameter_example': json.loads(request.form.get('parameter_example', '{}')),
      })
      (source / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
      (source / 'action.py').write_text(request.form.get('code', ''), encoding='utf-8')
      publish_capability(source)
   except (ValueError, KeyError, json.JSONDecodeError) as exc:
      flash(str(exc), 'error')
   else:
      flash('Aktion veröffentlicht. Sie kann nun zugeordnet und eingeplant werden.', 'success')
   return redirect(url_for('admin_tasks') + '#editor')


@app.post('/admin/examples/install')
@admin_required
def install_examples():
   check_csrf()
   installed = 0
   for source in sorted((BASE / 'examples' / 'capabilities').iterdir()):
      if source.is_dir() and (source / 'manifest.json').is_file():
         publish_capability(source)
         installed += 1
   with core.db() as conn:
      conn.execute('''INSERT INTO capability_assignments(
            capability_id, target_type, target_id, enabled, execution)
         VALUES('client-info-minimal', 'all', '*', 1, 'hourly')
         ON CONFLICT(capability_id, target_type, target_id) DO NOTHING''')
   bump_generation()
   flash('%d Beispielaktionen veröffentlicht; bitte den gewünschten Clients zuordnen.' % installed, 'success')
   return redirect(url_for('admin_tasks') + '#capabilities')


@app.post('/admin/assignment')
@admin_required
def save_assignment():
   check_csrf()
   capabilities = request.form.getlist('capability')
   enabled_states = request.form.getlist('enabled')
   executions = request.form.getlist('execution') or ['manual'] * len(capabilities)
   if len(capabilities) != len(enabled_states) or len(capabilities) != len(executions):
      abort(400)
   targets = ['device:' + device_id for device_id in request.form.getlist('device_ids')]
   targets = targets or [request.form.get('target', '')]
   with core.db() as conn:
      for capability, enabled, execution in zip(capabilities, enabled_states, executions):
         for target in targets:
            if target == 'all':
               target_type, target_id = 'all', '*'
            else:
               target_type, target_id = target.split(':', 1)
            conn.execute('''INSERT INTO capability_assignments(
                  capability_id, target_type, target_id, enabled, execution)
               VALUES(?,?,?,?,?) ON CONFLICT(capability_id, target_type, target_id)
               DO UPDATE SET enabled=excluded.enabled, execution=excluded.execution''',
               (capability, target_type, target_id, int(enabled), execution))
   bump_generation()
   flash('Aufgaben-Zuordnungen gespeichert.', 'success')
   destination = url_for('admin_clients') + '#devices' if request.form.get('next') == 'clients' else url_for('admin_tasks')
   return redirect(destination)


@app.post('/admin/action')
@admin_required
def create_action():
   check_csrf()
   try:
      parameters = json.loads(request.form.get('parameters', '{}'))
      targets = request.form.getlist('targets') or [request.form.get('target', '')]
      devices = []
      for target in targets:
         devices.extend(device for device in core.resolve_devices(target) if device not in devices)
      remember = len(targets) == 1 and request.form.get('remember') == '1' and targets[0].startswith('group:')
      if not devices and not remember:
         raise ValueError('Kein Client für dieses Ziel gefunden.')
      capability_id = request.form.get('capability', '')
      capability = next((item for item in load_manifest().get('capabilities', [])
                         if item.get('id') == capability_id), {})
      scope = capability.get('scope', 'system')
      username = request.form.get('username', '').strip()
      if remember:
         with core.db() as conn:
            group_name = targets[0].split(':', 1)[1]
            conn.execute('''INSERT INTO action_templates(
               group_name, capability_id, parameters_json, scope, username, created_at)
               VALUES(?,?,?,?,?,?)''', (group_name, capability_id,
               json.dumps(parameters, ensure_ascii=False), scope, username, int(time.time())))
      run_at = int(request.form.get('run_at') or time.time())
      for target_device in devices:
         core.queue_action(target_device['id'], capability_id, parameters, run_at,
                           scope, username)
   except (ValueError, json.JSONDecodeError) as exc:
      flash(str(exc), 'error')
   else:
      flash('%d Aktion(en) eingeplant%s.' % (len(devices),
            ' und für neue Gruppenmitglieder vorgemerkt' if remember else ''), 'success')
   return redirect(url_for('admin_logging'))


def main():
   RELEASES.mkdir(parents=True, exist_ok=True)
   app.run(host=HOST, port=PORT, threaded=True)


if __name__ == '__main__':
   main()
