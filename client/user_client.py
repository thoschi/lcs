import getpass
import json
import os
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from capability_runtime import load_stack, run_capability
from common.config import load_env
from common.http_client import request_json

VERSION = '0.6.0'


def config_path():
   if os.name == 'nt':
      base = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS'
      return Path(os.environ.get('LCS_CONFIG', str(base / 'client.env')))
   return Path(os.environ.get('LCS_CONFIG', '/opt/lcs-service/client.env'))


def runtime_paths(config):
   if os.name == 'nt':
      base = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS'
      user_dir = Path(os.environ.get('APPDATA', str(Path.home()))) / 'LCS'
      state_root = Path(config.get('LCS_STATE_ROOT', str(base / 'state')))
      feature_root = Path(config.get('LCS_FEATURE_ROOT', str(base / 'features')))
   else:
      state_root = Path(config.get('LCS_STATE_ROOT', '/opt/lcs-service/state'))
      feature_root = Path(config.get('LCS_FEATURE_ROOT', '/opt/lcs-service/features'))
      user_dir = Path.home() / '.config' / 'lcs'
   user_file = Path(config.get('LCS_USER_STATE', str(user_dir / 'user.json')))
   return state_root / 'device-public.json', user_file, feature_root


def load_device(path):
   try:
      data = json.loads(path.read_text(encoding='utf-8'))
      return data.get('device_id')
   except Exception:
      return None


def remember_username(path, username):
   path.parent.mkdir(parents=True, exist_ok=True)
   path.write_text(json.dumps({'username': username}, indent=2), encoding='utf-8')


def remembered_username(path):
   try:
      return json.loads(path.read_text(encoding='utf-8')).get('username', '')
   except Exception:
      return ''


def login(config, device_id, username, password):
   return request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/login',
      {'device_id': device_id, 'username': username, 'password': password, 'user_client_version': VERSION},
      ca_file=config.get('LCS_CA_FILE') or None)


def session_heartbeat(config, token):
   return request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/heartbeat', {},
      headers={'Authorization': 'Bearer ' + token}, ca_file=config.get('LCS_CA_FILE') or None)


def report_result(config, token, capability_id, result):
   try:
      request_json(
         'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/action/result',
         {'capability_id': capability_id, 'result': result},
         headers={'Authorization': 'Bearer ' + token}, ca_file=config.get('LCS_CA_FILE') or None)
   except Exception:
      pass


def user_capabilities(feature_root):
   stack = load_stack(feature_root)
   caps = []
   for cap in stack.get('capabilities', []):
      if cap.get('scope') == 'user' and ('user' in cap.get('tags', []) or not cap.get('tags')):
         caps.append(cap)
   return stack.get('generation', 0), sorted(caps, key=lambda c: c.get('title', c['id']).lower())


def run_cli(config, device_id, user_file, feature_root):
   username = remembered_username(user_file) or input('Benutzername: ').strip()
   password = getpass.getpass('Passwort: ')
   status, response = login(config, device_id, username, password)
   if status != 200:
      print('Anmeldung fehlgeschlagen:', response.get('error', response))
      return 1
   remember_username(user_file, username)
   print('Angemeldet als', username)
   generation, caps = user_capabilities(feature_root)
   print('Lokaler Fähigkeits-Stack Generation', generation)
   for index, cap in enumerate(caps, 1):
      print('%2d. %s' % (index, cap.get('title', cap['id'])))
   return 0


def run_gui(config, device_id, user_file, feature_root):
   import tkinter as tk
   from tkinter import messagebox

   root = tk.Tk()
   root.title('LCS Benutzer')
   root.minsize(470, 300)

   session = {'token': None, 'username': '', 'generation': None, 'running': True}

   login_frame = tk.Frame(root, padx=18, pady=18)
   login_frame.pack(fill='both', expand=True)
   tk.Label(login_frame, text='LCS Benutzeranmeldung', font=('', 16, 'bold')).pack(anchor='w', pady=(0, 14))

   form = tk.Frame(login_frame)
   form.pack(fill='x')
   tk.Label(form, text='Benutzername', width=14, anchor='w').grid(row=0, column=0, pady=5)
   username = tk.Entry(form, width=32)
   username.grid(row=0, column=1, pady=5, sticky='ew')
   username.insert(0, remembered_username(user_file))
   tk.Label(form, text='Passwort', width=14, anchor='w').grid(row=1, column=0, pady=5)
   password = tk.Entry(form, width=32, show='*')
   password.grid(row=1, column=1, pady=5, sticky='ew')

   status_label = tk.Label(login_frame, text='', anchor='w')
   status_label.pack(fill='x', pady=(10, 4))
   login_button = tk.Button(login_frame, text='Anmelden')
   login_button.pack(anchor='e', pady=(5, 0))

   menu_frame = tk.Frame(root, padx=18, pady=18)
   header = tk.Label(menu_frame, text='', font=('', 15, 'bold'))
   header.pack(anchor='w')
   subheader = tk.Label(menu_frame, text='Verfügbare Optionen', anchor='w')
   subheader.pack(fill='x', pady=(3, 12))
   options_frame = tk.Frame(menu_frame)
   options_frame.pack(fill='both', expand=True)
   refresh_button = tk.Button(menu_frame, text='Optionen neu laden')
   refresh_button.pack(anchor='e', pady=(12, 0))

   def heartbeat_loop():
      while session['running'] and session['token']:
         try:
            session_heartbeat(config, session['token'])
         except Exception:
            pass
         time.sleep(30)

   def execute_capability(cap):
      def worker():
         try:
            result = run_capability(cap, {}, timeout=int(cap.get('timeout', 120)))
            report_result(config, session['token'], cap['id'], result)
            root.after(0, lambda: messagebox.showinfo(cap.get('title', cap['id']), 'Aktion abgeschlossen.'))
         except Exception as exc:
            message = str(exc)
            root.after(0, lambda message=message: messagebox.showerror(cap.get('title', cap['id']), message))
      threading.Thread(target=worker, daemon=True).start()

   def load_menu(force=False):
      generation, caps = user_capabilities(feature_root)
      if not force and session['generation'] is not None and generation == session['generation']:
         return
      session['generation'] = generation
      for widget in options_frame.winfo_children():
         widget.destroy()
      if not caps:
         tk.Label(options_frame, text='Derzeit sind keine Benutzeraktionen verfügbar.', anchor='w').pack(fill='x')
      for cap in caps:
         row = tk.Frame(options_frame, pady=4)
         row.pack(fill='x')
         text = tk.Frame(row)
         text.pack(side='left', fill='x', expand=True)
         tk.Label(text, text=cap.get('title', cap['id']), font=('', 11, 'bold'), anchor='w').pack(fill='x')
         if cap.get('description'):
            tk.Label(text, text=cap['description'], anchor='w', justify='left', wraplength=300).pack(fill='x')
         tk.Button(row, text='Ausführen', command=lambda c=cap: execute_capability(c)).pack(side='right', padx=(10, 0))

   def check_stack_change():
      if not session['running']:
         return
      if session['token']:
         generation, _ = user_capabilities(feature_root)
         if session['generation'] is not None and generation != session['generation']:
            if messagebox.askyesno('Neue Optionen', 'Neue oder geänderte Optionen sind verfügbar. Jetzt neu laden?'):
               load_menu(force=True)
            else:
               session['generation'] = generation
      root.after(5000, check_stack_change)

   def do_login():
      name = username.get().strip()
      secret = password.get()
      if not name or not secret:
         messagebox.showerror('Anmeldung', 'Benutzername und Passwort sind erforderlich.')
         return
      status_label.configure(text='Anmeldung läuft …')
      root.update_idletasks()
      try:
         code, response = login(config, device_id, name, secret)
      except Exception as exc:
         status_label.configure(text='')
         messagebox.showerror('Anmeldung', 'Server nicht erreichbar: %s' % exc)
         return
      if code != 200:
         status_label.configure(text='')
         messagebox.showerror('Anmeldung', response.get('error', 'Anmeldung fehlgeschlagen.'))
         return
      remember_username(user_file, name)
      session['token'] = response['session_token']
      session['username'] = name
      password.delete(0, tk.END)
      login_frame.pack_forget()
      header.configure(text='Angemeldet als ' + (response.get('full_name') or name))
      menu_frame.pack(fill='both', expand=True)
      load_menu(force=True)
      threading.Thread(target=heartbeat_loop, daemon=True).start()

   login_button.configure(command=do_login)
   refresh_button.configure(command=lambda: load_menu(force=True))
   password.bind('<Return>', lambda _event: do_login())
   root.after(5000, check_stack_change)

   def close():
      session['running'] = False
      session['token'] = None
      root.destroy()

   root.protocol('WM_DELETE_WINDOW', close)
   root.mainloop()
   return 0


def main():
   env_path = config_path()
   config = load_env(env_path)
   device_path, user_file, default_feature_root = runtime_paths(config)
   proxy = config.get('LCS_PROXY', '').strip()
   if proxy:
      os.environ['http_proxy'] = proxy
      os.environ['https_proxy'] = proxy
      os.environ['HTTP_PROXY'] = proxy
      os.environ['HTTPS_PROXY'] = proxy
   if not config.get('LCS_SERVER'):
      print('LCS_SERVER fehlt in', env_path)
      return 2
   feature_root = Path(config.get('LCS_FEATURE_ROOT', str(default_feature_root)))
   device_id = load_device(device_path)
   if not device_id:
      print('Das Gerät wurde noch nicht vom System-Agenten registriert.')
      return 3
   if '--cli' in sys.argv:
      return run_cli(config, device_id, user_file, feature_root)
   try:
      return run_gui(config, device_id, user_file, feature_root)
   except Exception as exc:
      print('GUI nicht verfügbar:', exc)
      return run_cli(config, device_id, user_file, feature_root)


if __name__ == '__main__':
   raise SystemExit(main())
