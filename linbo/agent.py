"""Minimal LINBO client using the normal LCS device protocol."""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / 'system'))
from agent import run_forever

# The regular moderator supplies inventory, registration, queues and shutdown/reboot.
# LINBO adds only fixed, locally installed commands; arbitrary server code is rejected.
os.environ.setdefault('LCS_RUNTIME', 'linbo')

if __name__ == '__main__':
   run_forever(sys.argv[1] if len(sys.argv) > 1 else None)
