from pathlib import Path


def load_env(path):
   values = {}
   p = Path(path)
   if not p.exists():
      return values
   for raw in p.read_text(encoding='utf-8').splitlines():
      line = raw.strip()
      if not line or line.startswith('#') or '=' not in line:
         continue
      key, value = line.split('=', 1)
      values[key.strip()] = value.strip().strip('"').strip("'")
   return values


def env_bool(values, key, default=False):
   raw = values.get(key)
   if raw is None:
      return default
   return raw.lower() in ('1', 'true', 'yes', 'on')
