import json
import os
import secrets
import time
import tarfile
import hashlib
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
MANIFEST = Path(os.environ.get('LCS_MANIFEST_FILE', str(BASE / 'bootstrap-manifest.json')))
MAX_REQUEST_BYTES = int(os.environ.get('LCS_MAX_REQUEST_BYTES', str(2 * 1024 * 1024)))
SOURCE_ROOT = Path(os.environ.get('LCS_SOURCE_ROOT', '/opt/lcs'))
ADMIN_USERS = {value.strip() for value in os.environ.get('LCS_ADMIN_USERS', '').split(',') if value.strip()}

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
   selected = [cap for cap in payload.get('capabilities', [])
               if core.capability_enabled_for_device(device['id'], cap['id'])]
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
         SELECT a.*, d.hostname FROM actions a JOIN devices d ON d.id=a.device_id
         ORDER BY a.id DESC LIMIT 40
      ''').fetchall()]
   for item in devices:
      item['online'] = item['last_seen'] >= now - 60
      try:
         item['hardware'] = json.loads(item.get('hardware_json') or '{}')
      except json.JSONDecodeError:
         item['hardware'] = {}
   return devices, groups, assignments, tokens, actions


def render_admin(new_token=None, editor=None):
   devices, groups, assignments, tokens, actions = dashboard_data()
   return render_template('admin.html', devices=devices, groups=groups, assignments=assignments,
                          tokens=tokens, actions=actions, manifest=load_manifest(),
                          now=core.now_ts(), new_token=new_token, editor=editor or {})


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
   capability_id = request.args.get('edit', '').strip()
   if not capability_id:
      return render_admin()
   try:
      editor = load_capability_for_editor(capability_id)
   except (ValueError, zipfile.BadZipFile) as exc:
      flash(str(exc), 'error')
      return redirect(url_for('admin') + '#capabilities')
   return render_admin(editor=editor)


@app.get('/admin/client-status')
@admin_required
def client_status():
   devices, _, _, _, _ = dashboard_data()
   return jsonify(devices=[{
      'id': item['id'],
      'online': item['online'],
      'last_seen': item['last_seen'],
      'last_seen_text': format_datetime(item['last_seen']),
      'hostname': item['hostname'],
      'platform': item['platform'] or '',
      'agent_version': item['agent_version'] or '',
      'groups': item['groups'] or '',
      'hardware': item['hardware'],
      'is_image_source': bool(item.get('is_image_source')),
   } for item in devices], now=core.now_ts())


@app.post('/admin/group')
@admin_required
def save_group():
   check_csrf()
   name = request.form.get('name', '').strip()
   if not name:
      abort(400, 'Gruppenname fehlt')
   with core.db() as conn:
      conn.execute('''INSERT INTO groups(name, description) VALUES(?,?)
         ON CONFLICT(name) DO UPDATE SET description=excluded.description''',
         (name, request.form.get('description', '').strip()))
   flash('Gruppe gespeichert.', 'success')
   return redirect(url_for('admin') + '#groups')


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
   return redirect(url_for('admin') + '#devices')


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
   return redirect(url_for('admin') + '#devices')


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
   return redirect(url_for('admin') + '#devices')


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
   return redirect(url_for('admin') + '#groups')


@app.post('/admin/action-template/<int:template_id>/delete')
@admin_required
def delete_action_template(template_id):
   check_csrf()
   with core.db() as conn:
      conn.execute('DELETE FROM action_templates WHERE id=?', (template_id,))
   flash('Vorbereitete Aufgabe entfernt.', 'success')
   return redirect(url_for('admin') + '#groups')


@app.post('/admin/token')
@admin_required
def create_token():
   check_csrf()
   try:
      settings = core.enrollment_settings(
         request.form.get('user_data', ''), request.form.get('require_local_username') == '1',
         request.form.get('password_username', ''))
      core.add_enrollment_token(request.form.get('name', ''), request.form.get('password', ''),
                                request.form.get('token_type', 'template') == 'template', settings=settings)
   except Exception as exc:
      flash(str(exc), 'error')
      return redirect(url_for('admin') + '#tokens')
   flash('Vorläufiger Zugang erzeugt. Er wird beim ersten Enrollment aktiviert.', 'success')
   return redirect(url_for('admin') + '#tokens')


@app.post('/admin/token/<int:token_id>/toggle')
@admin_required
def toggle_token(token_id):
   check_csrf()
   with core.db() as conn:
      conn.execute('UPDATE enrollment_tokens SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?',
                   (token_id,))
   flash('Token-Status geändert.', 'success')
   return redirect(url_for('admin') + '#tokens')


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
   return redirect(url_for('admin') + '#tokens')


@app.post('/admin/capability/<capability_id>')
@admin_required
def edit_capability(capability_id):
   check_csrf()
   payload = load_manifest()
   cap = next((item for item in payload.get('capabilities', []) if item.get('id') == capability_id), None)
   if not cap:
      abort(404)
   cap['title'] = request.form.get('title', '').strip() or capability_id
   cap['description'] = request.form.get('description', '').strip()
   cap['timeout'] = max(1, int(request.form.get('timeout', 120)))
   cap['user_executable'] = request.form.get('user_executable') == '1'
   bump_generation(payload)
   flash('Capability aktualisiert.', 'success')
   return redirect(url_for('admin') + '#capabilities')


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
   return redirect(url_for('admin') + '#editor')


@app.post('/admin/examples/install')
@admin_required
def install_examples():
   check_csrf()
   installed = 0
   for source in sorted((BASE / 'examples' / 'capabilities').iterdir()):
      if source.is_dir() and (source / 'manifest.json').is_file():
         publish_capability(source)
         installed += 1
   flash('%d Beispielaktionen veröffentlicht; bitte den gewünschten Clients zuordnen.' % installed, 'success')
   return redirect(url_for('admin') + '#capabilities')


@app.post('/admin/assignment')
@admin_required
def save_assignment():
   check_csrf()
   capability = request.form.get('capability', '')
   target = request.form.get('target', '')
   if target == 'all':
      target_type, target_id = 'all', '*'
   else:
      target_type, target_id = target.split(':', 1)
   with core.db() as conn:
      conn.execute('''INSERT INTO capability_assignments(capability_id, target_type, target_id, enabled)
         VALUES(?,?,?,?) ON CONFLICT(capability_id, target_type, target_id)
         DO UPDATE SET enabled=excluded.enabled''',
         (capability, target_type, target_id, int(request.form.get('enabled', '1'))))
   bump_generation()
   flash('Capability-Zuordnung gespeichert.', 'success')
   return redirect(url_for('admin') + '#capabilities')


@app.post('/admin/action')
@admin_required
def create_action():
   check_csrf()
   try:
      parameters = json.loads(request.form.get('parameters', '{}'))
      target = request.form.get('target', '')
      devices = core.resolve_devices(target)
      remember = request.form.get('remember') == '1' and target.startswith('group:')
      if not devices and not remember:
         raise ValueError('Kein Client für dieses Ziel gefunden.')
      capability_id = request.form.get('capability', '')
      capability = next((item for item in load_manifest().get('capabilities', [])
                         if item.get('id') == capability_id), {})
      scope = capability.get('scope', 'system')
      username = request.form.get('username', '').strip()
      if remember:
         with core.db() as conn:
            group_name = target.split(':', 1)[1]
            conn.execute('''INSERT INTO action_templates(
               group_name, capability_id, parameters_json, scope, username, created_at)
               VALUES(?,?,?,?,?,?)''', (group_name, capability_id,
               json.dumps(parameters, ensure_ascii=False), scope, username, int(time.time())))
            if not capability_id.startswith('__lcs_'):
               conn.execute('''INSERT INTO capability_assignments(capability_id, target_type, target_id, enabled)
                  VALUES(?, 'group', ?, 1) ON CONFLICT(capability_id, target_type, target_id)
                  DO UPDATE SET enabled=1''', (capability_id, group_name))
         bump_generation()
      for target_device in devices:
         core.queue_action(target_device['id'], capability_id, parameters, int(time.time()),
                           scope, username)
   except (ValueError, json.JSONDecodeError) as exc:
      flash(str(exc), 'error')
   else:
      flash('%d Aktion(en) eingeplant%s.' % (len(devices),
            ' und für neue Gruppenmitglieder vorgemerkt' if remember else ''), 'success')
   return redirect(url_for('admin') + '#actions')


def main():
   core.init_db()
   RELEASES.mkdir(parents=True, exist_ok=True)
   app.run(host=HOST, port=PORT, threaded=True)


if __name__ == '__main__':
   main()
