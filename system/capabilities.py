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

LINBO_CAPABILITIES = tuple(
   {'id': 'linbo_%s_%d' % (command, position), 'version': '3',
    'title': '%s %d' % (title, position),
    'description': '%s Betriebssystem %d.' % (description, position), 'user_executable': False}
   for command, title, description in (
      ('start', 'Starten', 'Startet'),
      ('sync', 'Synchronisieren', 'Synchronisiert'),
      ('format', 'Formatieren', 'Formatiert'),
      ('new', 'Neu', 'Erstellt neu'),
   )
   for position in range(1, 4)
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
      match = re.fullmatch(r'linbo_(start|sync|format|new)_([1-3])', capability_id)
      if not match:
         raise RuntimeError('Ungültiger LINBO-Befehl.')
      command = ['linbo_wrapper', '%s:%s' % match.groups()]
   elif capability_id == 'shutdown':
      command = ['shutdown', '/s', '/t', '0'] if os.name == 'nt' else ['systemctl', 'poweroff']
   elif capability_id == 'reboot':
      command = ['shutdown', '/r', '/t', '0'] if os.name == 'nt' else ['systemctl', 'reboot']
   elif os.name == 'nt':
      # The service runs as SYSTEM. logoff.exe therefore needs the interactive session id.
      import win32ts
      session_id = ''
      for session in win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE, 1, 0):
         session_user = win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, session['SessionId'], win32ts.WTSUserName)
         if session_user and (not username or session_user.lower() == username.lower()):
            session_id = str(session['SessionId'])
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
