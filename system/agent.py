import json
import os
import re
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from capability_runtime import capability_map, load_stack, run_capability, sync_stack
from common.config import load_env
from common.http_client import request_json
from common.platform_info import hardware_info, logged_in_users

VERSION = '0.6.0'


def default_paths():
   if os.name == 'nt':
      base = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS'
      return {
         'env': os.environ.get('LCS_CONFIG', str(base / 'client.env')),
         'state_dir': str(base / 'state'),
         'feature_root': str(base / 'features'),
         'token': str(base / 'enrollment.token'),
      }
   return {
      'env': os.environ.get('LCS_CONFIG', str(BASE / 'client.env')),
      'state_dir': str(BASE / 'state'),
      'feature_root': str(BASE / 'features'),
      'token': str(BASE / 'enrollment.token'),
   }


def runtime_paths(config):
   defaults = default_paths()
   return {
      'env': defaults['env'],
      'state_dir': config.get('LCS_STATE_ROOT', defaults['state_dir']),
      'feature_root': config.get('LCS_FEATURE_ROOT', defaults['feature_root']),
      'token': config.get('LCS_TOKEN_FILE', defaults['token']),
   }


def user_profile_path(config):
   local_username = config.get('LCS_PASSWORD_USERNAME', 'nutzer').strip() or 'nutzer'
   default = (str(Path(os.environ.get('SystemDrive', 'C:')) / 'Users' / local_username / 'AppData' / 'Roaming' / 'LCS')
              if os.name == 'nt' else '/home/%s/.config/lcs' % local_username)
   root = Path(config.get('LCS_USER_DATA', default)).expanduser()
   return root / 'credentials.json'


def system_marker_path(config):
   default = (str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'system-initialized')
              if os.name == 'nt' else '/var/lib/lcs/system-initialized')
   return Path(config.get('LCS_SYSTEM_MARKER', default))


def initialization_status(config):
   profile = load_json(user_profile_path(config), {})
   try:
      user_marker = user_profile_path(config).with_name('system-marker').read_text(encoding='utf-8').strip()
      system_marker = system_marker_path(config).read_text(encoding='utf-8').strip()
   except Exception:
      user_marker = system_marker = ''
   required = not user_marker or user_marker != system_marker
   profile_exists = bool(profile.get('username')) and (os.name == 'nt' or bool(profile.get('shadow')))
   return {'profile_exists': profile_exists, 'username': profile.get('username', ''),
           'initialization_required': required, 'password_required': required and os.name == 'nt'}


def shadow_entry(username):
   for line in Path('/etc/shadow').read_text(encoding='utf-8').splitlines():
      fields = line.split(':')
      if fields[0] == username:
         return line
   raise RuntimeError('Lokales Benutzerkonto nicht gefunden: ' + username)


def restore_shadow_entry(username, entry):
   fields = entry.split(':')
   # Alte Profile enthielten nur den Hash; neue sichern die vollständige Shadow-Zeile.
   password_hash = fields[1] if len(fields) == 9 and fields[0] == username else entry
   if not re.fullmatch(r'[A-Za-z0-9_.-]+', username) or ':' in password_hash or '\n' in password_hash:
      raise RuntimeError('Ungültige Profildaten')
   result = subprocess.run(['chpasswd', '-e'], input=username + ':' + password_hash,
                           text=True, capture_output=True)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or 'Passworthash konnte nicht wiederhergestellt werden.')


def disable_autologin():
   if os.name == 'nt':
      import winreg
      key_path = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
      with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
         winreg.SetValueEx(key, 'AutoAdminLogon', 0, winreg.REG_SZ, '0')
         for name in ('DefaultPassword', 'DefaultUserName'):
            try:
               winreg.DeleteValue(key, name)
            except FileNotFoundError:
               pass
      return
   for filename in ('/etc/gdm3/custom.conf', '/etc/gdm/custom.conf', '/etc/lightdm/lightdm.conf', '/etc/sddm.conf'):
      path = Path(filename)
      if not path.is_file():
         continue
      text = path.read_text(encoding='utf-8')
      text = re.sub(r'(?im)^\s*AutomaticLoginEnable\s*=.*$', 'AutomaticLoginEnable=False', text)
      text = re.sub(r'(?im)^\s*(autologin-user|AutomaticLogin)\s*=.*$', r'# \1 disabled by LCS', text)
      if 'gdm' in filename and not re.search(r'(?im)^\s*AutomaticLoginEnable\s*=', text):
         daemon = re.search(r'(?im)^\s*\[daemon\]\s*$', text)
         if daemon:
            text = text[:daemon.end()] + '\nAutomaticLoginEnable=False' + text[daemon.end():]
         else:
            text += '\n[daemon]\nAutomaticLoginEnable=False\n'
      path.write_text(text, encoding='utf-8')


def initialize_user(config, username='', password=''):
   profile_path = user_profile_path(config)
   local_username = config.get('LCS_PASSWORD_USERNAME', 'nutzer').strip() or 'nutzer'
   status = initialization_status(config)
   profile = load_json(profile_path, {})
   if not status['initialization_required']:
      return {'ok': True, 'username': profile.get('username', '')}
   if status['profile_exists'] and os.name != 'nt':
      restore_shadow_entry(local_username, str(profile.get('shadow', '')))
   elif not password or (not status['profile_exists'] and not username):
      return {'ok': False, 'error': 'Benutzername und Passwort sind erforderlich.'}
   elif os.name == 'nt':
      result = subprocess.run(['net', 'user', local_username, password], capture_output=True, text=True)
      if result.returncode:
         return {'ok': False, 'error': result.stderr.strip() or result.stdout.strip() or 'Passwort konnte nicht gesetzt werden.'}
   else:
      if not re.fullmatch(r'[A-Za-z0-9_.@-]+', username):
         return {'ok': False, 'error': 'Ungültiger Benutzername.'}
      result = subprocess.run(['chpasswd'], input=local_username + ':' + password, text=True, capture_output=True)
      if result.returncode:
         return {'ok': False, 'error': result.stderr.strip() or 'Passwort konnte nicht gesetzt werden.'}
   username = profile.get('username', '') if status['profile_exists'] else username
   if not re.fullmatch(r'[A-Za-z0-9_.@-]+', username):
      return {'ok': False, 'error': 'Ungültiger Benutzername.'}
   disable_autologin()
   stored = {'username': username}
   if os.name != 'nt':
      stored['shadow'] = shadow_entry(local_username)
   save_json(profile_path, stored, 0o600)
   marker = os.urandom(24).hex()
   profile_path.with_name('system-marker').write_text(marker + '\n', encoding='utf-8')
   system_marker = system_marker_path(config)
   system_marker.parent.mkdir(parents=True, exist_ok=True)
   system_marker.write_text(marker + '\n', encoding='utf-8')
   return {'ok': True, 'username': username}


def user_capabilities(stack):
   return [{'id': cap['id'], 'title': cap.get('title', cap['id']), 'description': cap.get('description', '')}
           for cap in stack.get('capabilities', [])
           if cap.get('scope') == 'system' and cap.get('user_executable')]


def handle_user_request(config, runtime, request):
   operation = request.get('operation')
   if operation == 'status':
      return {'ok': True, 'client_enabled': runtime.get('client_enabled', False),
              **initialization_status(config)}
   if not runtime.get('client_enabled', False):
      return {'ok': False, 'error': 'Der Nutzerclient ist für einen Musterclient deaktiviert.'}
   if operation == 'initialize':
      return initialize_user(config, str(request.get('username', '')).strip(), str(request.get('password', '')))
   if operation == 'capabilities':
      return {'ok': True, 'capabilities': user_capabilities(runtime['stack'])}
   if operation == 'execute':
      cap_id = str(request.get('capability_id', ''))
      cap = next((item for item in runtime['stack'].get('capabilities', [])
                  if item.get('id') == cap_id and item.get('scope') == 'system' and item.get('user_executable')), None)
      if not cap:
         return {'ok': False, 'error': 'Aktion ist nicht für Benutzer freigegeben.'}
      local_username = config.get('LCS_PASSWORD_USERNAME', 'nutzer').strip() or 'nutzer'
      if os.name == 'nt':
         user_home = Path(os.environ.get('SystemDrive', 'C:')) / 'Users' / local_username
      else:
         import pwd
         user_home = Path(pwd.getpwnam(local_username).pw_dir)
      result = run_capability(cap, {}, timeout=int(cap.get('timeout', 120)),
                              context={'username': local_username, 'user_home': str(user_home),
                                       'data_path': str(user_profile_path(config).parent)})
      return {'ok': int(result.get('exit_code', 0)) == 0, 'result': result,
              'error': result.get('stderr', '') if int(result.get('exit_code', 0)) else ''}
   return {'ok': False, 'error': 'Unbekannte Anfrage.'}


def serve_user_client(config, runtime):
   default_socket = (str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'user.sock')
                     if os.name == 'nt' else '/run/lcs/user.sock')
   path = Path(config.get('LCS_USER_SOCKET', default_socket))
   path.parent.mkdir(parents=True, exist_ok=True)
   path.unlink(missing_ok=True)
   with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
      listener.bind(str(path))
      os.chmod(path, 0o666)
      listener.listen(8)
      while True:
         connection, _ = listener.accept()
         with connection:
            try:
               if hasattr(socket, 'SO_PEERCRED'):
                  _pid, uid, _gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                  import pwd
                  allowed_user = config.get('LCS_PASSWORD_USERNAME', 'nutzer').strip() or 'nutzer'
                  if uid not in (0, pwd.getpwnam(allowed_user).pw_uid):
                     raise PermissionError('Zugriff auf den LCS-Systemdienst verweigert.')
               raw = b''
               while b'\n' not in raw and len(raw) < 1024 * 1024:
                  chunk = connection.recv(65536)
                  if not chunk:
                     break
                  raw += chunk
               request = json.loads(raw.split(b'\n', 1)[0].decode('utf-8'))
               response = handle_user_request(config, runtime, request)
            except Exception as exc:
               response = {'ok': False, 'error': str(exc)}
            connection.sendall(json.dumps(response, ensure_ascii=False).encode('utf-8') + b'\n')


def load_json(path, default=None):
   try:
      return json.loads(Path(path).read_text(encoding='utf-8'))
   except Exception:
      return default if default is not None else {}


def save_json(path, payload, mode=None):
   path = Path(path)
   path.parent.mkdir(parents=True, exist_ok=True)
   tmp = path.with_suffix(path.suffix + '.tmp')
   tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
   os.replace(tmp, path)
   if mode is not None:
      try:
         os.chmod(path, mode)
      except Exception:
         pass


def load_state(state_dir):
   return load_json(Path(state_dir) / 'device.json', {})


def save_state(state_dir, state):
   save_json(Path(state_dir) / 'device.json', state, 0o600)
   save_json(Path(state_dir) / 'device-public.json', {
      'device_id': state.get('device_id', ''),
      'image_source': bool(state.get('image_source')),
   }, 0o644)


def save_server_settings(env_path, settings):
   allowed = ('LCS_USER_DATA', 'LCS_REQUIRE_LOCAL_USERNAME', 'LCS_PASSWORD_USERNAME')
   path = Path(env_path)
   try:
      lines = path.read_text(encoding='utf-8').splitlines()
   except FileNotFoundError:
      lines = []
   lines = [line for line in lines if not any(line.startswith(key + '=') for key in allowed)]
   for key in allowed:
      value = str(settings.get(key, '')).replace('\r', '').replace('\n', '')
      if value:
         lines.append(key + '=' + value)
   path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def read_enrollment_token(config):
   token_path = Path(runtime_paths(config)['token'])
   try:
      return token_path.read_text(encoding='utf-8').strip(), token_path
   except Exception:
      return '', token_path


def enroll(config, state_dir):
   info = hardware_info()
   enrollment_token, token_path = read_enrollment_token(config)
   if not enrollment_token:
      raise RuntimeError('Enrollment token missing: %s' % token_path)
   payload = {
      'enrollment_token': enrollment_token,
      'hostname': info['hostname'],
      'machine_id': info['machine_id'],
      'platform': info['platform'],
      'agent_version': VERSION,
   }
   status, response = request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/enroll', payload,
      ca_file=config.get('LCS_CA_FILE') or None)
   if status != 200:
      raise RuntimeError('Enrollment failed: %s' % response)
   save_server_settings(runtime_paths(config)['env'], response.get('settings', {}))
   state = {'device_id': response['device_id'], 'device_token': response['device_token'],
            'hostname': info['hostname'], 'image_source': bool(response.get('image_source'))}
   save_state(state_dir, state)
   try:
      if not state['image_source']:
         token_path.unlink(missing_ok=True)
   except Exception as exc:
      print('warning: could not remove enrollment token:', exc, flush=True)
   return state


def auth_headers(state):
   return {
      'Authorization': 'Bearer ' + state['device_token'],
      'X-Device-ID': state['device_id'],
   }


def post_device(config, state, path, payload):
   return request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + path, payload,
      headers=auth_headers(state), ca_file=config.get('LCS_CA_FILE') or None)


def heartbeat(config, state, stack):
   payload = {
      'agent_version': VERSION,
      'hardware': hardware_info(),
      'logged_in_users': logged_in_users(),
      'stack_generation': int(stack.get('generation', 0)),
   }
   return post_device(config, state, '/api/v1/heartbeat', payload)


def template_heartbeat(config, state):
   return post_device(config, state, '/api/v1/heartbeat', {
      'agent_version': VERSION,
      'stack_generation': 0,
   })


def apply_server_role(state, response, state_dir, user_runtime):
   image_source = response.get('role') == 'template'
   if state.get('image_source') != image_source:
      state['image_source'] = image_source
      save_state(state_dir, state)
   user_runtime['client_enabled'] = bool(response.get('client_enabled', not image_source))


def report_event(config, state, event_type, capability_id, payload, state_dir=None):
   event = {
      'event_type': event_type,
      'capability_id': capability_id,
      'payload': payload,
   }
   try:
      status, _ = post_device(config, state, '/api/v1/event', event)
      if status == 200:
         return
   except Exception:
      pass
   if state_dir:
      path = Path(state_dir) / 'event-outbox.json'
      items = load_json(path, [])
      items.append(event)
      save_json(path, items, 0o600)


def flush_events(config, state, state_dir):
   path = Path(state_dir) / 'event-outbox.json'
   items = load_json(path, [])
   if not items:
      return
   remaining = []
   for index, event in enumerate(items):
      try:
         status, _ = post_device(config, state, '/api/v1/event', event)
         if status != 200:
            remaining.append(event)
      except Exception:
         remaining.append(event)
         remaining.extend(items[index + 1:])
         break
   save_json(path, remaining, 0o600)

def boot_id():
   if os.name != 'nt':
      p = Path('/proc/sys/kernel/random/boot_id')
      if p.exists():
         return p.read_text().strip()
   return str(int(time.time() - time.monotonic()))


def trigger_due(trigger, cap, scheduler_state, now):
   key = cap['id'] + '@' + cap['version'] + ':' + json.dumps(trigger, sort_keys=True)
   previous = scheduler_state.get(key, {})
   kind = trigger.get('type')
   if kind == 'startup':
      current_boot = boot_id()
      if previous.get('boot_id') != current_boot:
         return True, key, {'boot_id': current_boot, 'last_run': now}
   elif kind == 'interval':
      seconds = max(1, int(trigger.get('seconds', 3600)))
      if now - int(previous.get('last_run', 0)) >= seconds:
         return True, key, {'last_run': now}
   elif kind == 'daily':
      at = str(trigger.get('at', '00:00'))
      current = time.strftime('%H:%M', time.localtime(now))
      today = time.strftime('%Y-%m-%d', time.localtime(now))
      if current >= at and previous.get('date') != today:
         return True, key, {'date': today, 'last_run': now}
   return False, key, previous


def run_scheduled_system_capabilities(config, state, stack, state_dir):
   scheduler_path = Path(state_dir) / 'scheduler.json'
   scheduler = load_json(scheduler_path, {})
   changed = False
   now = int(time.time())
   for cap in stack.get('capabilities', []):
      if cap.get('scope') != 'system':
         continue
      for trigger in cap.get('triggers', []):
         due, key, new_state = trigger_due(trigger, cap, scheduler, now)
         if not due:
            continue
         try:
            result = run_capability(cap, trigger.get('parameters', {}), timeout=int(cap.get('timeout', 120)))
            report_event(config, state, 'scheduled_result', cap['id'], result, state_dir)
         except Exception as exc:
            report_event(config, state, 'scheduled_error', cap['id'], {'error': str(exc)}, state_dir)
         scheduler[key] = new_state
         changed = True
   if changed:
      save_json(scheduler_path, scheduler, 0o600)


def save_action_result(state_dir, payload):
   path = Path(state_dir) / 'result-outbox.json'
   items = load_json(path, [])
   items.append(payload)
   save_json(path, items, 0o600)


def flush_action_results(config, state, state_dir):
   path = Path(state_dir) / 'result-outbox.json'
   items = load_json(path, [])
   if not items:
      return
   remaining = []
   for payload in items:
      try:
         status, _ = post_device(config, state, '/api/v1/action/result', payload)
         if status != 200:
            remaining.append(payload)
      except Exception:
         remaining.append(payload)
         remaining.extend(items[items.index(payload) + 1:])
         break
   save_json(path, remaining, 0o600)


def poll_manual_actions(config, state, stack, state_dir):
   pending_path = Path(state_dir) / 'pending-actions.json'
   pending = {str(item['id']): item for item in load_json(pending_path, [])}
   status, response = request_json(
      'GET', config['LCS_SERVER'].rstrip('/') + '/api/v1/agent/poll',
      headers=auth_headers(state), ca_file=config.get('LCS_CA_FILE') or None, timeout=8)
   if status != 200:
      return status
   for action in response.get('actions', []):
      pending[str(action['id'])] = action
   save_json(pending_path, list(pending.values()), 0o600)
   return status


def execute_due_actions(config, state, stack, state_dir):
   pending_path = Path(state_dir) / 'pending-actions.json'
   pending = {str(item['id']): item for item in load_json(pending_path, [])}
   capabilities = capability_map(stack, 'system')
   now = int(time.time())
   completed = []
   for key, action in list(pending.items()):
      if int(action.get('run_at', 0)) > now:
         continue
      action_id = action['id']
      cap_id = action['capability_id']
      ok = True
      if cap_id == '__lcs_reset_device__':
         payload = {'action_id': action_id, 'ok': True, 'result': {'message': 'device reset acknowledged'}}
         try:
            status, _ = post_device(config, state, '/api/v1/action/result', payload)
         except Exception as exc:
            print('device reset acknowledgement unavailable:', exc, flush=True)
            return
         # 401 means that the server processed an earlier acknowledgement and
         # already removed the device before the response reached this client.
         if status not in (200, 401):
            print('device reset acknowledgement rejected:', status, flush=True)
            return
         reset_device(config, state, action.get('parameters', {}).get('reenrollment_token', ''))
         return
      if cap_id in ('__lcs_update_git__', '__lcs_update_bundle__'):
         try:
            result = schedule_update(config, state, cap_id == '__lcs_update_bundle__')
         except Exception as exc:
            ok = False
            result = {'error': str(exc)}
         payload = {'action_id': action_id, 'ok': ok, 'result': result}
         try:
            status, _ = post_device(config, state, '/api/v1/action/result', payload)
            if status != 200:
               save_action_result(state_dir, payload)
         except Exception:
            save_action_result(state_dir, payload)
         completed.append(key)
         continue
      cap = capabilities.get(cap_id)
      if not cap:
         ok = False
         result = {'error': 'system capability not available locally: ' + cap_id}
      else:
         try:
            result = run_capability(cap, action.get('parameters', {}), timeout=int(cap.get('timeout', 120)))
            ok = int(result.get('exit_code', 0)) == 0
         except Exception as exc:
            ok = False
            result = {'error': str(exc)}
      payload = {'action_id': action_id, 'ok': ok, 'result': result}
      try:
         status, _ = post_device(config, state, '/api/v1/action/result', payload)
         if status != 200:
            save_action_result(state_dir, payload)
      except Exception:
         save_action_result(state_dir, payload)
      completed.append(key)
   for key in completed:
      pending.pop(key, None)
   if completed:
      save_json(pending_path, list(pending.values()), 0o600)


def schedule_update(config, state, from_server=False):
   if os.name == 'nt':
      raise RuntimeError('Die integrierte Aktualisierung ist derzeit nur unter Linux verfügbar')
   source_root = Path(config.get('LCS_SOURCE_ROOT', '/opt/lcs'))
   if from_server:
      archive = Path(tempfile.gettempdir()) / 'lcs-update.tar.gz'
      request = urllib.request.Request(config['LCS_SERVER'].rstrip('/') + '/api/v1/update/source',
                                       headers=auth_headers(state))
      context = None
      ca_file = config.get('LCS_CA_FILE')
      if ca_file:
         import ssl
         context = ssl.create_default_context(cafile=ca_file)
      with urllib.request.urlopen(request, timeout=120, context=context) as response, archive.open('wb') as target:
         target.write(response.read())
      with tarfile.open(archive, 'r:gz') as package:
         for member in package.getmembers():
            if member.name.startswith('/') or '..' in Path(member.name).parts:
               raise RuntimeError('Unsicherer Pfad im Update-Paket')
      update = 'mkdir -p {root} && tar -xzf {archive} -C {root}'.format(
         root=shlex_quote(str(source_root)), archive=shlex_quote(str(archive)))
      method = 'server bundle'
   else:
      update = 'git -C {root} pull --ff-only'.format(root=shlex_quote(str(source_root)))
      method = 'git pull'
   installer = '{root}/install.sh upgrade workstation {server}'.format(
      root=shlex_quote(str(source_root)), server=shlex_quote(config['LCS_SERVER']))
   command = 'sleep 2; {update} && {installer}'.format(update=update, installer=installer)
   subprocess.Popen(['/bin/systemd-run', '--unit=lcs-upgrade', '--collect', '/bin/bash', '-c', command],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
   return {'message': 'update scheduled', 'method': method}


def shlex_quote(value):
   import shlex
   return shlex.quote(value)

def reset_device(config, state, reenrollment_token=''):
   paths = runtime_paths(config)
   token_path = Path(config.get('LCS_TOKEN_FILE', paths['token']))
   if reenrollment_token:
      token_path.parent.mkdir(parents=True, exist_ok=True)
      token_path.write_text(reenrollment_token + '\n', encoding='utf-8')
      try:
         os.chmod(token_path, 0o600)
      except Exception:
         pass
   else:
      token_path.unlink(missing_ok=True)
   for filename in ('device.json', 'device-public.json', 'scheduler.json', 'pending-actions.json', 'result-outbox.json', 'event-outbox.json'):
      try:
         (Path(paths['state_dir']) / filename).unlink(missing_ok=True)
      except Exception:
         pass
   try:
      feature_root = Path(config.get('LCS_FEATURE_ROOT', paths['feature_root']))
      if feature_root.exists():
         import shutil
         shutil.rmtree(feature_root)
      feature_root.mkdir(parents=True, exist_ok=True)
   except Exception:
      pass
   if os.name == 'nt':
      subprocess.Popen(['sc.exe', 'stop', 'LCSService'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
   else:
      subprocess.Popen(['/bin/systemctl', 'stop', 'lcs-service.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
   raise SystemExit(0)


def run_forever(env_path=None, stop_requested=None):
   defaults = default_paths()
   env_path = env_path or defaults['env']
   config = load_env(env_path)
   paths = runtime_paths(config)
   proxy = config.get('LCS_PROXY', '').strip()
   if proxy:
      os.environ['http_proxy'] = proxy
      os.environ['https_proxy'] = proxy
      os.environ['HTTP_PROXY'] = proxy
      os.environ['HTTPS_PROXY'] = proxy
   if not config.get('LCS_SERVER'):
      raise RuntimeError('LCS_SERVER missing in %s' % env_path)
   config.setdefault('LCS_FEATURE_ROOT', paths['feature_root'])
   heartbeat_interval = int(config.get('LCS_HEARTBEAT_SECONDS', '20'))
   poll_interval = int(config.get('LCS_POLL_SECONDS', '10'))
   sync_interval = int(config.get('LCS_SYNC_SECONDS', '60'))
   state = load_state(paths['state_dir'])
   if state.get('device_id'):
      save_json(Path(paths['state_dir']) / 'device-public.json', {
         'device_id': state['device_id'],
         'image_source': bool(state.get('image_source')),
      }, 0o644)
   stack = load_stack(config['LCS_FEATURE_ROOT'])
   user_runtime = {'stack': stack,
                   'client_enabled': bool(state.get('device_id') and not state.get('image_source'))}
   threading.Thread(target=serve_user_client, args=(config, user_runtime), daemon=True).start()
   last_heartbeat = 0
   last_poll = 0
   last_sync = 0

   while True:
      now = time.time()
      if stop_requested and stop_requested():
         return

      current_hostname = socket.gethostname()
      if state.get('hostname') and state['hostname'].lower() != current_hostname.lower():
         # Hostnamen können erst nach dem Start des geklonten Systems gesetzt werden.
         state = {}
         user_runtime['client_enabled'] = False
         for filename in ('device.json', 'device-public.json'):
            (Path(paths['state_dir']) / filename).unlink(missing_ok=True)

      if not state.get('device_id') or not state.get('device_token'):
         try:
            state = enroll(config, paths['state_dir'])
            user_runtime['client_enabled'] = not state.get('image_source')
         except Exception as exc:
            print('enrollment unavailable:', exc, flush=True)
            time.sleep(3)
            continue

      if state.get('image_source'):
         if now - last_heartbeat >= heartbeat_interval:
            try:
               status, response = template_heartbeat(config, state)
               if status == 401:
                  state = {}
                  user_runtime['client_enabled'] = False
                  last_heartbeat = now
                  continue
               if status != 200:
                  print('template heartbeat failed:', response, flush=True)
               else:
                  apply_server_role(state, response, paths['state_dir'], user_runtime)
            except Exception as exc:
               print('template heartbeat unavailable:', exc, flush=True)
            last_heartbeat = now
         time.sleep(1)
         continue

      if now - last_sync >= sync_interval:
         try:
            changed, new_stack = sync_stack(config, state)
            stack = new_stack
            user_runtime['stack'] = stack
            if changed:
               report_event(config, state, 'stack_updated', '', {'generation': stack.get('generation', 0)}, paths['state_dir'])
         except Exception as exc:
            print('stack sync unavailable; using local stack:', exc, flush=True)
         last_sync = now

      # Lokale Trigger und bereits vorab geladene zeitgesteuerte Aktionen
      # laufen auch dann weiter, wenn der Managementserver nicht erreichbar ist.
      run_scheduled_system_capabilities(config, state, stack, paths['state_dir'])
      execute_due_actions(config, state, stack, paths['state_dir'])

      if now - last_poll >= poll_interval:
         try:
            flush_events(config, state, paths['state_dir'])
            flush_action_results(config, state, paths['state_dir'])
            code = poll_manual_actions(config, state, stack, paths['state_dir'])
            if code == 401:
               state = {}
               user_runtime['client_enabled'] = False
               last_poll = now
               continue
         except Exception as exc:
            print('action poll unavailable:', exc, flush=True)
         last_poll = now

      if now - last_heartbeat >= heartbeat_interval:
         try:
            status, response = heartbeat(config, state, stack)
            if status == 401:
               state = {}
               user_runtime['client_enabled'] = False
               last_heartbeat = now
               continue
            if status != 200:
               print('heartbeat failed:', response, flush=True)
            else:
               apply_server_role(state, response, paths['state_dir'], user_runtime)
         except Exception as exc:
            print('heartbeat unavailable:', exc, flush=True)
         last_heartbeat = now

      time.sleep(1)


if __name__ == '__main__':
   run_forever(sys.argv[1] if len(sys.argv) > 1 else None)
