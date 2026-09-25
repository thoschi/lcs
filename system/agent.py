import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from capabilities import execute as execute_capability, public_capabilities
from common.config import env_bool, load_env
from common.http_client import request_json
from common.platform_info import hostname, logged_in_users, system_information
from executor import Executor

VERSION = '0.8.0'


def log(message, **fields):
   details = ' '.join('%s=%s' % (key, json.dumps(value, ensure_ascii=False))
                      for key, value in fields.items())
   print('%s [lcs-agent] %s%s' % (time.strftime('%Y-%m-%dT%H:%M:%S%z'), message,
                                  (' ' + details) if details else ''), flush=True)


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


def user_data_path(config, username=''):
   local_username = username or config['LCS_PASSWORD_USERNAME'].strip()
   default = (str(Path(os.environ.get('SystemDrive', 'C:')) / 'Users' / local_username / 'AppData' / 'Roaming' / 'LCS')
              if os.name == 'nt' else '/home/%s/.config/lcs' % local_username)
   configured = config.get('LCS_USER_DATA', default)
   return Path(configured.replace('${username}', local_username).replace('$username', local_username)).expanduser()


def user_profile_path(config, username=''):
   return user_data_path(config, username) / 'credentials.json'


def system_marker_path(config):
   default = (str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'system-initialized')
              if os.name == 'nt' else '/var/lib/lcs/system-initialized')
   return Path(config.get('LCS_SYSTEM_MARKER', default))


def user_marker_path(config, username=''):
   platform = 'windows' if os.name == 'nt' else 'linux'
   return user_profile_path(config, username).with_name('system-marker-' + platform)


def initialization_status(config, local_username=''):
   if env_bool(config, 'LCS_USE_DOMAIN_USERNAME'):
      profile = load_json(user_profile_path(config, local_username), {})
      profile_exists = profile.get('username') == local_username
      return {'profile_exists': profile_exists, 'username': local_username,
              'initialization_required': not profile_exists,
              'password_required': False, 'domain_username': True}
   profile = load_json(user_profile_path(config, local_username), {})
   try:
      user_marker = user_marker_path(config, local_username).read_text(encoding='utf-8').strip()
      system_marker = system_marker_path(config).read_text(encoding='utf-8').strip()
   except Exception:
      user_marker = system_marker = ''
   profile_exists = bool(profile.get('username')) and (os.name == 'nt' or bool(profile.get('shadow')))
   required = not profile_exists or not user_marker or user_marker != system_marker
   username = profile.get('username', '')
   username_known = bool(username)
   return {'profile_exists': profile_exists, 'username_known': username_known, 'username': username,
           'initialization_required': required,
           'password_required': required and (os.name == 'nt' or username_known and not profile_exists)}


def refresh_initialization_status(config, runtime):
   """Keep the local-account decision in the privileged service."""
   if runtime.get('image_source') or env_bool(config, 'LCS_USE_DOMAIN_USERNAME'):
      return
   status = initialization_status(config)
   runtime['initialization_status'] = status


def shadow_entry(username):
   for line in Path('/etc/shadow').read_text(encoding='utf-8').splitlines():
      fields = line.split(':')
      if fields[0] == username:
         return line
   raise RuntimeError('Lokales Benutzerkonto nicht gefunden: ' + username)


def restore_shadow_entry(username, entry):
   log('Passworthash wird wiederhergestellt', local_username=username)
   fields = entry.split(':')
   # Alte Profile enthielten nur den Hash; neue sichern die vollständige Shadow-Zeile.
   password_hash = fields[1] if len(fields) == 9 and fields[0] == username else entry
   if not re.fullmatch(r'[A-Za-z0-9_.-]+', username) or ':' in password_hash or '\n' in password_hash:
      raise RuntimeError('Ungültige Profildaten')
   result = subprocess.run(['chpasswd', '-e'], input=username + ':' + password_hash,
                           text=True, capture_output=True)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or 'Passworthash konnte nicht wiederhergestellt werden.')
   log('Passworthash wurde wiederhergestellt', local_username=username)


def clear_linux_keyrings(username):
   """Discard keyrings inherited from the image before the first personal login."""
   import pwd
   keyrings = Path(pwd.getpwnam(username).pw_dir) / '.local' / 'share' / 'keyrings'
   if keyrings.is_symlink():
      keyrings.unlink()
   elif keyrings.exists():
      shutil.rmtree(keyrings)
   log('Geerbte Desktop-Schlüsselbunde wurden entfernt', local_username=username)


def disable_autologin():
   log('Autologin wird deaktiviert', platform=os.name)
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
      log('Autologin wurde deaktiviert', configuration=key_path)
      return
   changed = []
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
      changed.append(filename)
   log('Autologin wurde deaktiviert', configurations=changed)


def initialize_user(config, username='', password='', force=False, client_username=''):
   profile_path = user_profile_path(config, client_username)
   if env_bool(config, 'LCS_USE_DOMAIN_USERNAME'):
      status = initialization_status(config, client_username)
      if not force and not status['initialization_required']:
         return {'ok': True, 'username': client_username}
      if not client_username:
         return {'ok': False, 'error': 'Domänenbenutzer konnte nicht ermittelt werden.'}
      disable_autologin()
      save_json(profile_path, {'username': client_username}, 0o600)
      log('Domänenbenutzereinrichtung abgeschlossen', username=client_username)
      return {'ok': True, 'username': client_username}
   local_username = config['LCS_PASSWORD_USERNAME'].strip()
   status = initialization_status(config, client_username)
   profile = load_json(profile_path, {})
   log('Benutzereinrichtung geprüft', initialization_required=status['initialization_required'],
       profile_exists=status['profile_exists'], local_username=local_username)
   if not force and not status['initialization_required']:
      log('Benutzereinrichtung bereits abgeschlossen')
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
      clear_linux_keyrings(local_username)
   username = profile.get('username', '') if status['profile_exists'] else username
   if not re.fullmatch(r'[A-Za-z0-9_.@-]+', username):
      return {'ok': False, 'error': 'Ungültiger Benutzername.'}
   disable_autologin()
   stored = {'username': username}
   if os.name != 'nt':
      stored['shadow'] = shadow_entry(local_username)
   elif profile.get('shadow'):
      stored['shadow'] = profile['shadow']
   save_json(profile_path, stored, 0o600)
   marker = os.urandom(24).hex()
   user_marker_path(config, client_username).write_text(marker + '\n', encoding='utf-8')
   system_marker = system_marker_path(config)
   system_marker.parent.mkdir(parents=True, exist_ok=True)
   system_marker.write_text(marker + '\n', encoding='utf-8')
   log('Benutzereinrichtung abgeschlossen', username=username, local_username=local_username)
   return {'ok': True, 'username': username}


def user_capabilities(stack):
   return [cap for cap in public_capabilities() if cap.get('user_executable')]


def handle_user_request(config, runtime, request, peer_username=''):
   operation = request.get('operation')
   domain_username = peer_username or str(request.get('local_username', '')).strip()
   if operation == 'status':
      if runtime.get('image_source'):
         status = {'initialization_required': False}
      else:
         status = initialization_status(config, domain_username)
         runtime['initialization_status'] = status
      if status.get('domain_username'):
         status['username'] = domain_username
      information = system_information(VERSION)
      information['capabilities'] = public_capabilities()
      return {'ok': True, 'client_enabled': runtime.get('client_enabled', False),
              'image_source': runtime.get('image_source', False),
              'runtime_ready': runtime.get('ready', True), 'system': information, **status}
   if operation == 'capabilities':
      return {'ok': True, 'capabilities': user_capabilities(runtime['stack'])}
   if operation == 'execute':
      cap_id = str(request.get('capability_id', ''))
      cap = next((item for item in public_capabilities()
                  if item.get('id') == cap_id and item.get('user_executable')), None)
      if not cap:
         return {'ok': False, 'error': 'Aktion ist nicht für Benutzer freigegeben.'}
      local_username = (domain_username if env_bool(config, 'LCS_USE_DOMAIN_USERNAME') else
                        config['LCS_PASSWORD_USERNAME'].strip())
      result = execute_capability(cap_id, local_username)
      return {'ok': True, 'result': result}
   if operation == 'initialize':
      if runtime.get('image_source'):
         return {'ok': False, 'error': 'Auf Musterclients ist keine Nutzereinrichtung vorgesehen.'}
      profile_username = domain_username
      result = initialize_user(config, str(request.get('username', '')).strip(),
                               str(request.get('password', '')), client_username=profile_username)
      runtime['initialization_status'] = initialization_status(config, profile_username)
      return result
   return {'ok': False, 'error': 'Unbekannte Anfrage.'}


def allow_windows_pipe_users(pipe_handle):
   import win32con
   import win32security
   security = win32security.GetSecurityInfo(
      pipe_handle, win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION)
   dacl = security.GetSecurityDescriptorDacl() or win32security.ACL()
   dacl.AddAccessAllowedAce(
      win32security.ACL_REVISION,
      win32con.GENERIC_READ | win32con.GENERIC_WRITE,
      win32security.ConvertStringSidToSid('S-1-5-11'))
   win32security.SetSecurityInfo(
      pipe_handle, win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION,
      None, None, dacl, None)


def serve_user_client(config, runtime):
   if os.name == 'nt':
      import _winapi
      import win32con
      from multiprocessing import connection as pipe_connection

      class UserPipeListener(pipe_connection.PipeListener):
         def _new_handle(self, first=False):
            # Die ACL kann nur mit diesen Zugriffsrechten am Handle gesetzt werden.
            flags = (_winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED |
                     win32con.WRITE_DAC)
            if first:
               flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            handle = _winapi.CreateNamedPipe(
               self._address, flags,
               _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT,
               _winapi.PIPE_UNLIMITED_INSTANCES, pipe_connection.BUFSIZE, pipe_connection.BUFSIZE,
               _winapi.NMPWAIT_WAIT_FOREVER, _winapi.NULL)
            try:
               allow_windows_pipe_users(handle)
            except Exception:
               _winapi.CloseHandle(handle)
               raise
            return handle

      address = config.get('LCS_USER_SOCKET', r'\\.\pipe\lcs-user')
      log('Benutzerschnittstelle wird gestartet', address=address)
      listener = UserPipeListener(address)
      try:
         while True:
            connection = listener.accept()
            with connection:
               try:
                  request = json.loads(connection.recv_bytes().decode('utf-8'))
                  operation = request.get('operation')
                  if operation != 'status' or time.time() - runtime.get('last_status_log', 0) >= 60:
                     log('Benutzeranfrage empfangen', operation=operation,
                         local_username=request.get('local_username', ''))
                     runtime['last_status_log'] = time.time()
                  response = handle_user_request(config, runtime, request)
               except Exception as exc:
                  log('Benutzeranfrage fehlgeschlagen', error='%s: %s' % (type(exc).__name__, exc))
                  response = {'ok': False, 'error': str(exc)}
               connection.send_bytes(json.dumps(response, ensure_ascii=False).encode('utf-8'))
      finally:
         listener.close()
      return

   path = Path(config.get('LCS_USER_SOCKET', '/run/lcs/user.sock'))
   log('Benutzerschnittstelle wird gestartet', address=str(path))
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
               peer_username = ''
               if hasattr(socket, 'SO_PEERCRED'):
                  _pid, uid, _gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                  import pwd
                  peer_username = pwd.getpwuid(uid).pw_name
                  allowed_uid = pwd.getpwnam(config['LCS_PASSWORD_USERNAME'].strip()).pw_uid
                  if (not env_bool(config, 'LCS_USE_DOMAIN_USERNAME') and
                        uid not in (0, allowed_uid)):
                     raise PermissionError('Zugriff auf den LCS-Systemdienst verweigert.')
               raw = b''
               while b'\n' not in raw and len(raw) < 1024 * 1024:
                  chunk = connection.recv(65536)
                  if not chunk:
                     break
                  raw += chunk
               request = json.loads(raw.split(b'\n', 1)[0].decode('utf-8'))
               operation = request.get('operation')
               if operation != 'status' or time.time() - runtime.get('last_status_log', 0) >= 60:
                  log('Benutzeranfrage empfangen', operation=operation,
                      local_username=peer_username or request.get('local_username', ''))
                  runtime['last_status_log'] = time.time()
               response = handle_user_request(config, runtime, request, peer_username)
            except Exception as exc:
               log('Benutzeranfrage fehlgeschlagen', error='%s: %s' % (type(exc).__name__, exc))
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
   allowed = ('LCS_USER_DATA', 'LCS_USE_DOMAIN_USERNAME', 'LCS_PASSWORD_USERNAME', 'LCS_TEMPLATE_HOSTNAME')
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
      lines = token_path.read_text(encoding='utf-8').splitlines()
      return (lines[0].strip() if lines else '', lines[1].strip() if len(lines) > 1 else '', token_path)
   except Exception:
      return '', '', token_path


def enroll(config, state_dir):
   current_hostname = hostname()
   enrollment_token, template_hostname, token_path = read_enrollment_token(config)
   log('Registrierung gestartet', hostname=current_hostname, token_file=str(token_path))
   if not enrollment_token:
      raise RuntimeError('Enrollment token missing: %s' % token_path)
   log('Enrollment-Token geladen', token_file=str(token_path))
   payload = {
      'enrollment_token': enrollment_token,
      'hostname': current_hostname,
      'platform': platform.system().lower(),
      'agent_version': VERSION,
   }
   status, response = request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/enroll', payload,
      ca_file=config.get('LCS_CA_FILE') or None)
   log('Registrierungsantwort empfangen', status=status)
   if status != 200:
      raise RuntimeError('Enrollment failed: %s' % response)
   save_server_settings(runtime_paths(config)['env'], response.get('settings', {}))
   log('Servereinstellungen gespeichert', keys=sorted(response.get('settings', {}).keys()))
   for key in ('LCS_USER_DATA', 'LCS_USE_DOMAIN_USERNAME', 'LCS_PASSWORD_USERNAME'):
      if response.get('settings', {}).get(key):
         config[key] = str(response['settings'][key])
   registered_hostname = str(response.get('hostname') or current_hostname)
   restart_required = False
   if registered_hostname.lower() != current_hostname.lower():
      log('Hostname wird wiederhergestellt', current=current_hostname, registered=registered_hostname)
      restart_required = set_hostname(registered_hostname)
   state = {'device_id': response['device_id'], 'device_token': response['device_token'],
            'hostname': registered_hostname, 'image_source': bool(response.get('image_source')),
            'template_hostname': template_hostname or response.get('template_hostname', '')}
   save_state(state_dir, state)
   log('Registrierung gespeichert', device_id=state['device_id'], hostname=registered_hostname,
       image_source=state['image_source'])
   try:
      if not state['image_source']:
         token_path.unlink(missing_ok=True)
         log('Enrollment-Token nach erfolgreicher Registrierung entfernt', token_file=str(token_path))
   except Exception as exc:
      log('Enrollment-Token konnte nicht entfernt werden', error=str(exc))
   if restart_required:
      subprocess.Popen(['shutdown', '/r', '/t', '0'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
      raise SystemExit(0)
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


def set_hostname(hostname):
   if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', hostname):
      raise RuntimeError('Ungültiger gespeicherter Hostname')
   command = (['powershell', '-NoProfile', '-Command',
               "Rename-Computer -NewName '%s' -Force" % hostname]
              if os.name == 'nt' else ['hostnamectl', 'set-hostname', hostname])
   result = subprocess.run(command, capture_output=True, text=True)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or 'Hostname konnte nicht wiederhergestellt werden.')
   return os.name == 'nt'


def heartbeat(config, state, stack, inventory):
   users = logged_in_users()
   now = time.time()
   refresh_seconds = int(config.get('LCS_INVENTORY_SECONDS', '900'))
   if not inventory.get('information') or now - inventory.get('updated_at', 0) >= refresh_seconds:
      inventory['information'] = system_information(VERSION, users)
      inventory['updated_at'] = now
   information = dict(inventory['information'])
   capabilities = public_capabilities()
   information.update({
      'capabilities': capabilities,
      'current_users': users,
      'current_user': ', '.join(users) if users else 'niemand angemeldet',
   })
   payload = {
      'agent_version': VERSION,
      'hostname': hostname(),
      'logged_in_users': users,
      'capabilities': capabilities,
      'hardware': information,
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
   user_runtime['image_source'] = image_source


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
      log('Manuelle Aktion empfangen', action_id=action['id'], capability_id=action['capability_id'],
          run_at=action.get('run_at', 0))
   save_json(pending_path, list(pending.values()), 0o600)
   return status


def execute_due_actions(config, state, stack, state_dir):
   pending_path = Path(state_dir) / 'pending-actions.json'
   pending = {str(item['id']): item for item in load_json(pending_path, [])}
   capabilities = {item['id']: item for item in public_capabilities()}
   now = int(time.time())
   completed = []
   executor = stack['system_executor']
   running = stack.setdefault('running_actions', {})
   for key, action in list(pending.items()):
      if int(action.get('run_at', 0)) > now:
         continue
      action_id = action['id']
      cap_id = action['capability_id']
      log('Aktion gestartet', action_id=action_id, capability_id=cap_id)
      if cap_id == '__lcs_reset_device__':
         payload = {'action_id': action_id, 'ok': True, 'result': {'message': 'device reset acknowledged'}}
         try:
            status, _ = post_device(config, state, '/api/v1/action/result', payload)
         except Exception as exc:
            log('Bestätigung der Geräterücksetzung nicht verfügbar', action_id=action_id, error=str(exc))
            return
         # 401 means that the server processed an earlier acknowledgement and
         # already removed the device before the response reached this client.
         if status not in (200, 401):
            log('Bestätigung der Geräterücksetzung abgelehnt', action_id=action_id, status=status)
            return
         reset_device(config, state, action.get('parameters', {}).get('reenrollment_token', ''))
         return
      cap = capabilities.get(cap_id)
      if not cap:
         outcome = {'ok': False, 'error': 'system capability not available locally: ' + cap_id}
      elif key not in running:
         running[key] = executor.submit(action)
         continue
      else:
         outcome = executor.take(running[key])
         if outcome is None:
            continue
         running.pop(key, None)
      ok = outcome.get('ok', False)
      result = outcome.get('result', {'error': outcome.get('error', 'Aktion fehlgeschlagen.')})
      payload = {'action_id': action_id, 'ok': ok, 'result': result}
      try:
         status, _ = post_device(config, state, '/api/v1/action/result', payload)
         if status != 200:
            save_action_result(state_dir, payload)
      except Exception:
         save_action_result(state_dir, payload)
      completed.append(key)
      log('Aktion abgeschlossen', action_id=action_id, capability_id=cap_id, ok=ok)
   for key in completed:
      pending.pop(key, None)
   if completed:
      save_json(pending_path, list(pending.values()), 0o600)


def reset_device(config, state, reenrollment_token=''):
   paths = runtime_paths(config)
   token_path = Path(config.get('LCS_TOKEN_FILE', paths['token']))
   log('Geräterücksetzung gestartet', device_id=state.get('device_id', ''),
       reenrollment_token=bool(reenrollment_token))
   if reenrollment_token:
      token_path.parent.mkdir(parents=True, exist_ok=True)
      token_path.write_text(reenrollment_token + '\n' + state.get('template_hostname', '') + '\n', encoding='utf-8')
      log('Token für erneute Registrierung gespeichert', token_file=str(token_path))
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
   log('Geräterücksetzung abgeschlossen; Dienst wird beendet')
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
   missing = [key for key in ('LCS_PASSWORD_USERNAME', 'LCS_DEFAULT_PASSWORD')
              if not config.get(key, '').strip()]
   if missing:
      raise RuntimeError('Erforderliche Einträge fehlen in %s: %s' %
                         (env_path, ', '.join(missing)))
   log('Dienst gestartet', config=str(env_path), server=config['LCS_SERVER'])
   config.setdefault('LCS_FEATURE_ROOT', paths['feature_root'])
   heartbeat_interval = int(config.get('LCS_HEARTBEAT_SECONDS', '20'))
   poll_interval = int(config.get('LCS_POLL_SECONDS', '10'))
   state = load_state(paths['state_dir'])
   _, token_template_hostname, _ = read_enrollment_token(config)
   template_hostname = token_template_hostname or config.get('LCS_TEMPLATE_HOSTNAME', '')
   offline_image_source = (template_hostname.strip().lower() ==
                           socket.gethostname().strip().lower())
   if state.get('device_id'):
      save_json(Path(paths['state_dir']) / 'device-public.json', {
         'device_id': state['device_id'],
         'image_source': bool(state.get('image_source')),
      }, 0o644)
   stack = {
      'generation': 0,
      'capabilities': public_capabilities(),
      'system_executor': Executor('system-executor', lambda action: execute_capability(
         action['capability_id'], parameters=action.get('parameters', {}))),
   }
   user_runtime = {'stack': stack,
                   'client_enabled': bool(state.get('device_id') and not offline_image_source),
                   'image_source': offline_image_source,
                   'role_resolved': bool(state.get('device_id') or template_hostname),
                   'ready': False}
   if env_bool(config, 'LCS_USE_DOMAIN_USERNAME'):
      user_runtime['initialization_status'] = {'initialization_required': True}
   refresh_initialization_status(config, user_runtime)
   threading.Thread(target=serve_user_client, args=(config, user_runtime), daemon=True).start()
   last_heartbeat = 0
   last_poll = 0
   inventory = {}

   while True:
      refresh_initialization_status(config, user_runtime)
      now = time.time()
      if stop_requested and stop_requested():
         log('Dienststopp angefordert')
         return

      current_hostname = socket.gethostname()
      if state.get('hostname') and state['hostname'].lower() != current_hostname.lower():
         # Hostnamen können erst nach dem Start des geklonten Systems gesetzt werden.
         state = {}
         log('Klon erkannt; lokale Geräteidentität wird verworfen', current_hostname=current_hostname)
         user_runtime['client_enabled'] = False
         user_runtime['image_source'] = offline_image_source
         user_runtime['role_resolved'] = bool(template_hostname)
         for filename in ('device.json', 'device-public.json'):
            (Path(paths['state_dir']) / filename).unlink(missing_ok=True)

      if not state.get('device_id') or not state.get('device_token'):
         try:
            state = enroll(config, paths['state_dir'])
            user_runtime['client_enabled'] = not state.get('image_source')
            user_runtime['image_source'] = bool(state.get('image_source'))
            user_runtime['role_resolved'] = True
            log('Registrierung erfolgreich', device_id=state['device_id'], image_source=state['image_source'])
         except Exception as exc:
            log('Registrierung nicht verfügbar; erneuter Versuch folgt', error=str(exc))
            time.sleep(3)
            continue

      # Registrierung, Serverkommunikation und lokale Nutzereinrichtung laufen
      # unabhängig. Die IPC-Verarbeitung erfolgt parallel im Benutzer-Thread.
      user_runtime['ready'] = True

      if state.get('image_source'):
         if now - last_heartbeat >= heartbeat_interval:
            try:
               status, response = template_heartbeat(config, state)
               if status == 401:
                  state = {}
                  user_runtime['client_enabled'] = False
                  user_runtime['role_resolved'] = False
                  user_runtime['ready'] = False
                  last_heartbeat = now
                  continue
               if status != 200:
                  log('Musterclient-Heartbeat fehlgeschlagen', status=status, response=response)
               else:
                  apply_server_role(state, response, paths['state_dir'], user_runtime)
            except Exception as exc:
               log('Musterclient-Heartbeat nicht verfügbar', error=str(exc))
            last_heartbeat = now
         time.sleep(1)
         continue

      # Bereits angenommene Befehle bleiben auch bei einem Serverausfall ausführbar.
      execute_due_actions(config, state, stack, paths['state_dir'])

      if now - last_poll >= poll_interval:
         try:
            flush_events(config, state, paths['state_dir'])
            flush_action_results(config, state, paths['state_dir'])
            code = poll_manual_actions(config, state, stack, paths['state_dir'])
            if code == 401:
               state = {}
               user_runtime['client_enabled'] = False
               user_runtime['role_resolved'] = False
               user_runtime['ready'] = False
               last_poll = now
               continue
         except Exception as exc:
            log('Aktionsabfrage nicht verfügbar', error=str(exc))
         last_poll = now

      if now - last_heartbeat >= heartbeat_interval:
         try:
            status, response = heartbeat(config, state, stack, inventory)
            if status == 401:
               state = {}
               user_runtime['client_enabled'] = False
               user_runtime['role_resolved'] = False
               user_runtime['ready'] = False
               last_heartbeat = now
               continue
            if status != 200:
               log('Heartbeat fehlgeschlagen', status=status, response=response)
            else:
               apply_server_role(state, response, paths['state_dir'], user_runtime)
         except Exception as exc:
            log('Heartbeat nicht verfügbar', error=str(exc))
         last_heartbeat = now

      time.sleep(1)


if __name__ == '__main__':
   run_forever(sys.argv[1] if len(sys.argv) > 1 else None)
