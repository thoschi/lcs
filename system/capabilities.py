"""Built-in LCS capabilities.

Capabilities are part of the installed service version.  This module deliberately
does not load source code, manifests or packages from the management server.
"""
import os
import platform
import re
import subprocess


CAPABILITIES = (
   {'id': 'shutdown', 'version': '1', 'title': 'Herunterfahren',
    'description': 'Fährt diesen Computer herunter.', 'user_executable': True},
   {'id': 'reboot', 'version': '1', 'title': 'Neu starten',
    'description': 'Startet diesen Computer neu.', 'user_executable': True},
   {'id': 'logout', 'version': '1', 'title': 'Abmelden',
    'description': 'Meldet den aktuellen Benutzer ab.', 'user_executable': True},
)

LINBO_CAPABILITIES = (
   {'id': 'linbo_sync', 'version': '1', 'title': 'LINBO synchronisieren',
    'description': 'Synchronisiert und startet ein Betriebssystem.', 'user_executable': False},
   {'id': 'linbo_start', 'version': '1', 'title': 'LINBO starten',
    'description': 'Startet ein Betriebssystem.', 'user_executable': False},
)


def public_capabilities():
   """Return immutable metadata suitable for clients and the server."""
   items = CAPABILITIES + (LINBO_CAPABILITIES if os.environ.get('LCS_RUNTIME') == 'linbo' else ())
   return [dict(item) for item in items]


def execute(capability_id, username='', parameters=None):
   """Execute one installed operation without a shell or downloaded code."""
   if capability_id not in {item['id'] for item in public_capabilities()}:
      raise ValueError('Diese Fähigkeit ist lokal nicht installiert: ' + capability_id)
   if capability_id.startswith('linbo_'):
      os_name = str((parameters or {}).get('os', ''))
      if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', os_name):
         raise RuntimeError('Ungültige Betriebssystembezeichnung.')
      command = ['linbo_cmd', '-p' if capability_id == 'linbo_sync' else '-s', os_name]
   elif capability_id == 'shutdown':
      command = ['shutdown', '/s', '/t', '0'] if os.name == 'nt' else ['systemctl', 'poweroff']
   elif capability_id == 'reboot':
      command = ['shutdown', '/r', '/t', '0'] if os.name == 'nt' else ['systemctl', 'reboot']
   elif os.name == 'nt':
      # The service runs as SYSTEM. logoff.exe therefore needs the interactive session id.
      output = subprocess.check_output(['query', 'user'], text=True, errors='replace', timeout=5)
      session_id = ''
      for line in output.splitlines()[1:]:
         columns = line.replace('>', ' ').split()
         if columns and (not username or columns[0].lower() == username.lower()):
            session_id = next((value for value in columns[1:] if value.isdigit()), '')
            if session_id:
               break
      if not session_id:
         raise RuntimeError('Keine angemeldete Benutzersitzung gefunden.')
      command = ['logoff', session_id]
   else:
      if not username or not re.fullmatch(r'[A-Za-z0-9_.@\\-]+', username):
         raise RuntimeError('Kein gültiger angemeldeter Benutzer gefunden.')
      command = ['loginctl', 'terminate-user', username]
   result = subprocess.run(command, capture_output=True, text=True,
                           timeout=3600 if capability_id.startswith('linbo_') else 15)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Befehl fehlgeschlagen.')
   return {'message': capability_id + ' angefordert', 'platform': platform.system()}
