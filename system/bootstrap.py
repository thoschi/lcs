import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from agent import run_forever

BOOTSTRAP_VERSION = '0.5.0'


def run(env_path=None, stop_requested=None):
   run_forever(env_path, stop_requested=stop_requested)


if __name__ == '__main__':
   run(sys.argv[1] if len(sys.argv) > 1 else None)
