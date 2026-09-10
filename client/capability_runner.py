import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def password_is_set(username):
   """Return True on a known password, and fail closed if its state is unknown."""
   if os.name != 'posix' or not username:
      return True
   result = subprocess.run(['passwd', '-S', username], capture_output=True, text=True)
   if result.returncode != 0:
      return True
   fields = result.stdout.split()
   return len(fields) < 2 or fields[1] != 'NP'


def main():
   if len(sys.argv) < 2:
      raise SystemExit(2)
   package = Path(sys.argv[1]).resolve()
   request = json.loads(sys.stdin.read() or '{}')
   params = request.get('parameters', {})
   context = request.get('context', {})
   manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
   entrypoint = manifest.get('entrypoint', 'action.py')
   script = (package / entrypoint).resolve()
   if package != script and package not in script.parents:
      raise RuntimeError('Invalid capability entrypoint')
   for condition in manifest.get('conditions', []):
      if condition.get('type') == 'password_unset' and password_is_set(context.get('username', '')):
         print(json.dumps({'ok': True, 'skipped': True, 'reason': 'password_already_set'}))
         return
   spec = importlib.util.spec_from_file_location('lmn_capability', script)
   module = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(module)
   result = module.run({
      'parameters': params,
      'manifest': manifest,
      'package_path': str(package),
      'data_path': context.get('data_path', ''),
      'username': context.get('username', ''),
      'password': context.get('password', ''),
   })
   print(json.dumps(result if result is not None else {'ok': True}, ensure_ascii=False))


if __name__ == '__main__':
   main()
