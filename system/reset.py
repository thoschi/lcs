"""Fetch a fresh enrollment token before locally generalizing a client."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from agent import load_state, post_device, runtime_paths
from common.config import load_env


def main(env_path):
   config = load_env(env_path)
   required = ('LCS_SERVER', 'LCS_TOKEN_CHECKSUM')
   missing = [key for key in required if not config.get(key, '').strip()]
   if missing:
      raise RuntimeError('Erforderliche Einträge fehlen in %s: %s' %
                         (env_path, ', '.join(missing)))
   paths = runtime_paths(config)
   state = load_state(paths['state_dir'])
   if not state.get('device_id'):
      return 0
   status, response = post_device(config, state, '/api/v1/reset-token', {
      'checksum': config['LCS_TOKEN_CHECKSUM'],
   })
   if status != 200 or not response.get('enrollment_token'):
      raise RuntimeError(response.get('error', 'Reset-Token konnte nicht geladen werden.'))
   token = Path(paths['token'])
   token.parent.mkdir(parents=True, exist_ok=True)
   template_hostname = response.get('template_hostname', config['LCS_TEMPLATE_HOSTNAME'])
   token.write_text(response['enrollment_token'] + '\n' + template_hostname + '\n', encoding='utf-8')
   token.chmod(0o600)
   return 0


if __name__ == '__main__':
   raise SystemExit(main(sys.argv[1]))
