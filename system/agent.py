import json
import os
import subprocess
import sys
import time
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
   save_json(Path(state_dir) / 'device-public.json', {'device_id': state.get('device_id', '')}, 0o644)


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
   state = {'device_id': response['device_id'], 'device_token': response['device_token']}
   save_state(state_dir, state)
   try:
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
         payload = {'action_id': action_id, 'ok': True, 'result': {'message': 'device reset requested'}}
         try:
            post_device(config, state, '/api/v1/action/result', payload)
         except Exception:
            pass
         reset_device(config, state, action.get('parameters', {}).get('reenrollment_token', ''))
         return
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
   try:
      post_device(config, state, '/api/v1/device/self-delete', {})
   except Exception:
      pass
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
      save_json(Path(paths['state_dir']) / 'device-public.json', {'device_id': state['device_id']}, 0o644)
   stack = load_stack(config['LCS_FEATURE_ROOT'])
   last_heartbeat = 0
   last_poll = 0
   last_sync = 0

   while True:
      now = time.time()
      if stop_requested and stop_requested():
         return

      if not state.get('device_id') or not state.get('device_token'):
         try:
            state = enroll(config, paths['state_dir'])
         except Exception as exc:
            print('enrollment unavailable:', exc, flush=True)
            time.sleep(3)
            continue

      if now - last_sync >= sync_interval:
         try:
            changed, new_stack = sync_stack(config, state)
            stack = new_stack
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
               last_heartbeat = now
               continue
            if status != 200:
               print('heartbeat failed:', response, flush=True)
         except Exception as exc:
            print('heartbeat unavailable:', exc, flush=True)
         last_heartbeat = now

      time.sleep(1)


if __name__ == '__main__':
   run_forever(sys.argv[1] if len(sys.argv) > 1 else None)
