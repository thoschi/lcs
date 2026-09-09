import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from common.http_client import request_json


def stack_paths(feature_root):
   root = Path(feature_root)
   return {
      'root': root,
      'packages': root / 'packages',
      'stack': root / 'stack.json',
   }


def load_stack(feature_root):
   path = stack_paths(feature_root)['stack']
   if not path.exists():
      return {'generation': 0, 'capabilities': []}
   try:
      return json.loads(path.read_text(encoding='utf-8'))
   except Exception:
      return {'generation': 0, 'capabilities': []}


def _write_json_atomic(path, payload):
   path.parent.mkdir(parents=True, exist_ok=True)
   tmp = path.with_suffix(path.suffix + '.tmp')
   tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
   os.replace(tmp, path)


def _safe_extract(archive, destination):
   destination = destination.resolve()
   with zipfile.ZipFile(archive) as zf:
      for member in zf.infolist():
         target = (destination / member.filename).resolve()
         if destination != target and destination not in target.parents:
            raise RuntimeError('Unsafe path in capability package: ' + member.filename)
      zf.extractall(destination)


def _download(url, destination, headers=None, ca_file=None, timeout=15):
   import ssl
   req = urllib.request.Request(url, headers=headers or {})
   context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
   with urllib.request.urlopen(req, context=context, timeout=timeout) as response, destination.open('wb') as out:
      shutil.copyfileobj(response, out)


def sync_stack(config, state):
   feature_root = Path(config['LCS_FEATURE_ROOT'])
   paths = stack_paths(feature_root)
   paths['packages'].mkdir(parents=True, exist_ok=True)
   headers = {
      'Authorization': 'Bearer ' + state['device_token'],
      'X-Device-ID': state['device_id'],
   }
   server = config['LCS_SERVER'].rstrip('/')
   status, manifest = request_json(
      'GET', server + '/api/v1/bootstrap/manifest', headers=headers,
      ca_file=config.get('LCS_CA_FILE') or None, timeout=8)
   if status != 200:
      raise RuntimeError('Manifest sync failed: %s' % manifest)

   current = load_stack(feature_root)
   if int(manifest.get('generation', 0)) == int(current.get('generation', 0)):
      return False, current

   selected = []
   for item in manifest.get('capabilities', []):
      cap_id = item['id']
      version = item['version']
      target = paths['packages'] / cap_id / version
      marker = target / 'manifest.json'
      if not marker.exists():
         target.parent.mkdir(parents=True, exist_ok=True)
         with tempfile.TemporaryDirectory(prefix='lcs-cap-') as tmpdir:
            archive = Path(tmpdir) / 'package.zip'
            package_url = item.get('url') or ('/api/v1/bootstrap/package/' + item['filename'])
            if package_url.startswith('/'):
               package_url = server + package_url
            _download(package_url, archive, headers=headers, ca_file=config.get('LCS_CA_FILE') or None)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if digest.lower() != item['sha256'].lower():
               raise RuntimeError('SHA256 mismatch for capability %s' % cap_id)
            extract_tmp = Path(tmpdir) / 'extract'
            extract_tmp.mkdir()
            _safe_extract(archive, extract_tmp)
            package_manifest = extract_tmp / 'manifest.json'
            if not package_manifest.exists():
               raise RuntimeError('Capability %s has no manifest.json' % cap_id)
            parsed = json.loads(package_manifest.read_text(encoding='utf-8'))
            if parsed.get('id') != cap_id or parsed.get('version') != version:
               raise RuntimeError('Capability identity mismatch for %s' % cap_id)
            if target.exists():
               shutil.rmtree(target)
            shutil.move(str(extract_tmp), str(target))
      local = dict(item)
      local['path'] = str(target)
      selected.append(local)

   new_stack = {
      'generation': int(manifest.get('generation', 0)),
      'capabilities': selected,
   }
   _write_json_atomic(paths['stack'], new_stack)
   return True, new_stack


def run_capability(capability, parameters=None, timeout=120):
   package_path = Path(capability['path'])
   runner = BASE / 'capability_runner.py'
   command = [sys.executable, str(runner), str(package_path), json.dumps(parameters or {}, ensure_ascii=False)]
   result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
   output = result.stdout.strip()
   try:
      payload = json.loads(output) if output else {}
   except Exception:
      payload = {'stdout': output}
   if result.stderr.strip():
      payload['stderr'] = result.stderr.strip()
   payload['exit_code'] = result.returncode
   return payload


def capability_map(stack, scope=None):
   result = {}
   for cap in stack.get('capabilities', []):
      if scope and cap.get('scope') != scope:
         continue
      result[cap['id']] = cap
   return result
