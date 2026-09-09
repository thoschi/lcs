import json
import os
import secrets
import time
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
TOKEN_FILE = Path(os.environ.get('LCS_TOKEN_FILE', str(BASE / '.token')))
MAX_REQUEST_BYTES = int(os.environ.get('LCS_MAX_REQUEST_BYTES', str(2 * 1024 * 1024)))
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


def legacy_enrollment_token():
   try:
      return TOKEN_FILE.read_text(encoding='utf-8').strip()
   except OSError:
      return ''


def load_manifest():
   if not MANIFEST.exists():
      return {'generation': 0, 'capabilities': []}
   return json.loads(MANIFEST.read_text(encoding='utf-8'))


def write_manifest(payload):
   tmp = MANIFEST.with_suffix('.tmp')
   tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
   os.replace(tmp, MANIFEST)


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
         SELECT g.*, COUNT(dg.device_id) AS device_count
         FROM groups g LEFT JOIN device_groups dg ON dg.group_name=g.name
         GROUP BY g.name ORDER BY g.name
      ''').fetchall()]
      assignments = [dict(row) for row in conn.execute(
         'SELECT * FROM capability_assignments ORDER BY capability_id, target_type, target_id').fetchall()]
      tokens = [dict(row) for row in conn.execute(
         'SELECT * FROM enrollment_tokens ORDER BY created_at DESC').fetchall()]
      actions = [dict(row) for row in conn.execute('''
         SELECT a.*, d.hostname FROM actions a JOIN devices d ON d.id=a.device_id
         ORDER BY a.id DESC LIMIT 40
      ''').fetchall()]
   for item in devices:
      item['online'] = item['last_seen'] >= now - 60
   return devices, groups, assignments, tokens, actions


def render_admin(new_token=None):
   devices, groups, assignments, tokens, actions = dashboard_data()
   return render_template('admin.html', devices=devices, groups=groups, assignments=assignments,
                          tokens=tokens, actions=actions, manifest=load_manifest(),
                          now=core.now_ts(), new_token=new_token)


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


@app.get('/api/v1/agent/poll')
def agent_poll():
   return api_result(core.poll_actions(request.headers.get('X-Device-ID', ''), bearer()))


@app.post('/api/v1/<path:endpoint>')
def agent_api(endpoint):
   payload = request.get_json(silent=False) or {}
   device_id = request.headers.get('X-Device-ID', '')
   routes = {
      'enroll': lambda: core.enroll(payload),
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
      core.queue_action(device_id, '__lcs_reset_device__', {}, core.now_ts())
   except ValueError as exc:
      flash(str(exc), 'error')
   else:
      flash('Generalisierung eingeplant. Der Client wird erst nach seiner Bestätigung entfernt.', 'success')
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
      conn.execute("DELETE FROM capability_assignments WHERE target_type='group' AND target_id=?", (name,))
      conn.execute('DELETE FROM groups WHERE name=?', (name,))
   bump_generation()
   flash('Gruppe gelöscht.', 'success')
   return redirect(url_for('admin') + '#groups')


@app.post('/admin/token')
@admin_required
def create_token():
   check_csrf()
   try:
      token = core.add_enrollment_token(request.form.get('name', ''))
   except Exception as exc:
      flash(str(exc), 'error')
      return redirect(url_for('admin') + '#tokens')
   flash('Token erzeugt. Er wird nur jetzt vollständig angezeigt.', 'success')
   return render_admin(new_token=token)


@app.post('/admin/token/<int:token_id>/toggle')
@admin_required
def toggle_token(token_id):
   check_csrf()
   with core.db() as conn:
      conn.execute('UPDATE enrollment_tokens SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?',
                   (token_id,))
   flash('Token-Status geändert.', 'success')
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
   bump_generation(payload)
   flash('Capability aktualisiert.', 'success')
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
      devices = core.resolve_devices(request.form.get('target', ''))
      if not devices:
         raise ValueError('Kein Client für dieses Ziel gefunden.')
      for target_device in devices:
         core.queue_action(target_device['id'], request.form.get('capability', ''), parameters, int(time.time()))
   except (ValueError, json.JSONDecodeError) as exc:
      flash(str(exc), 'error')
   else:
      flash('%d Aktion(en) eingeplant.' % len(devices), 'success')
   return redirect(url_for('admin') + '#actions')


def main():
   core.init_db()
   RELEASES.mkdir(parents=True, exist_ok=True)
   legacy = legacy_enrollment_token()
   if legacy:
      core.import_enrollment_token('Legacy-Token', legacy)
   app.run(host=HOST, port=PORT, threaded=True)


if __name__ == '__main__':
   main()
