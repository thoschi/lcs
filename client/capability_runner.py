import importlib.util
import json
import sys
from pathlib import Path


def main():
   if len(sys.argv) < 2:
      raise SystemExit(2)
   package = Path(sys.argv[1]).resolve()
   params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
   manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
   entrypoint = manifest.get('entrypoint', 'action.py')
   script = (package / entrypoint).resolve()
   if package != script and package not in script.parents:
      raise RuntimeError('Invalid capability entrypoint')
   spec = importlib.util.spec_from_file_location('lmn_capability', script)
   module = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(module)
   result = module.run({
      'parameters': params,
      'manifest': manifest,
      'package_path': str(package),
   })
   print(json.dumps(result if result is not None else {'ok': True}, ensure_ascii=False))


if __name__ == '__main__':
   main()
