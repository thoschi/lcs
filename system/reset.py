"""Fetch a fresh enrollment token before locally generalizing a client."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from agent import load_state, post_device, runtime_paths
from common.config import load_env


def main(env_path):
   config = load_env(env_path)
   paths = runtime_paths(config)
   state = load_state(paths['state_dir'])
   if not state.get('device_id'):
      return 0
   status, response = post_device(config, state, '/api/v1/reset-token', {})
   if status != 200 or not response.get('enrollment_token'):
      raise RuntimeError(response.get('error', 'Reset-Token konnte nicht geladen werden.'))
   token = Path(paths['token'])
   token.parent.mkdir(parents=True, exist_ok=True)
   token.write_text(response['enrollment_token'] + '\n', encoding='utf-8')
   token.chmod(0o600)
   return 0


if __name__ == '__main__':
   raise SystemExit(main(sys.argv[1]))
