import os
import re
import subprocess
from pathlib import Path


def _linux(username, password):
   if not re.fullmatch(r'[A-Za-z0-9_.-]+', username):
      raise ValueError('Ungültiger Systembenutzername')
   result = subprocess.run(['chpasswd'], input=username + ':' + password, text=True,
                           capture_output=True)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or 'chpasswd fehlgeschlagen')
   changed = []
   for filename in ('/etc/gdm3/custom.conf', '/etc/gdm/custom.conf', '/etc/lightdm/lightdm.conf',
                    '/etc/sddm.conf'):
      path = Path(filename)
      if not path.is_file():
         continue
      text = path.read_text(encoding='utf-8')
      text = re.sub(r'(?im)^\s*AutomaticLoginEnable\s*=.*$', 'AutomaticLoginEnable=false', text)
      text = re.sub(r'(?im)^\s*(autologin-user|AutomaticLogin)\s*=.*$', r'# \1 disabled by LCS', text)
      path.write_text(text, encoding='utf-8')
      changed.append(filename)
   return changed


def _windows(username, password):
   result = subprocess.run(['net', 'user', username, password], capture_output=True, text=True)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'net user fehlgeschlagen')
   import winreg
   key_path = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
   with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
      winreg.SetValueEx(key, 'AutoAdminLogon', 0, winreg.REG_SZ, '0')
      for name in ('DefaultPassword', 'DefaultUserName'):
         try:
            winreg.DeleteValue(key, name)
         except FileNotFoundError:
            pass
   return ['Windows Winlogon']


def run(context):
   parameters = context['parameters']
   username = str(parameters.get('username', '')).strip()
   password = str(parameters.get('password', ''))
   if not username or not password:
      raise ValueError('Benutzername und Passwort fehlen')
   changed = _windows(username, password) if os.name == 'nt' else _linux(username, password)
   return {'username': username, 'password_set': True, 'autologin_disabled': True,
           'changed': changed}
