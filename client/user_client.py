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
from common.config import env_bool, load_env
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
   data_dir = Path(config.get('LCS_USER_DATA', str(user_dir / 'data'))).expanduser()
   return state_root / 'device-public.json', user_file, feature_root, data_dir


def prepare_user_data(data_dir):
   data_dir.mkdir(parents=True, exist_ok=True)
   for name in ('backgrounds', 'printers', 'state'):
      (data_dir / name).mkdir(exist_ok=True)
   return data_dir


def store_path(data_root, username):
   name = ''.join(c if c.isalnum() or c in '._-' else '_' for c in username).strip('._') or 'user'
   return data_root / name[:64]


def load_store(user_file, data_root):
   try:
      selected = json.loads(user_file.read_text(encoding='utf-8')).get('store', '')
      path = data_root / selected
      if not path.is_dir():
         path = data_root / 'stores' / selected
      profile = json.loads((path / 'credentials.json').read_text(encoding='utf-8'))
      if path.parent in (data_root, data_root / 'stores') and profile.get('username') and profile.get('password'):
         return path, profile
   except Exception:
      pass
   return None, {}


def save_store(user_file, data_root, username, password):
   path = prepare_user_data(store_path(data_root, username))
   credentials = path / 'credentials.json'
   credentials.write_text(json.dumps({'username': username, 'password': password}, indent=2), encoding='utf-8')
   user_file.parent.mkdir(parents=True, exist_ok=True)
   user_file.write_text(json.dumps({'store': path.name}, indent=2), encoding='utf-8')
   if os.name != 'nt':
      credentials.chmod(0o600)
      user_file.chmod(0o600)
   return path


def load_device(path):
   try:
      data = json.loads(path.read_text(encoding='utf-8'))
      return data.get('device_id')
   except Exception:
      return None


def login(config, device_id, username, password):
   return request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/login',
      {'device_id': device_id, 'username': username, 'password': password, 'user_client_version': VERSION},
      ca_file=config.get('LCS_CA_FILE') or None)


def local_username_allowed(config, username):
   return not env_bool(config, 'LCS_REQUIRE_LOCAL_USERNAME') or username.casefold() == getpass.getuser().casefold()


def session_heartbeat(config, token):
   return request_json(
      'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/heartbeat', {},
      headers={'Authorization': 'Bearer ' + token}, ca_file=config.get('LCS_CA_FILE') or None)


def report_result(config, token, capability_id, result, action_id=None):
   try:
      request_json(
         'POST', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/action/result',
         {'capability_id': capability_id, 'action_id': action_id, 'result': result,
          'ok': int(result.get('exit_code', 0)) == 0},
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


def poll_user_actions(config, token):
   return request_json('GET', config['LCS_SERVER'].rstrip('/') + '/api/v1/user/poll',
                       headers={'Authorization': 'Bearer ' + token},
                       ca_file=config.get('LCS_CA_FILE') or None, timeout=8)


def run_cli(config, device_id, user_file, feature_root, data_root):
   _store, profile = load_store(user_file, data_root)
   username = profile.get('username')
   password = profile.get('password')
   while not username or not password:
      username = input('Benutzername: ').strip()
      password = getpass.getpass('Passwort: ')
      if password != getpass.getpass('Passwort wiederholen: '):
         print('Die Passwörter stimmen nicht überein. Bitte erneut eingeben.')
         username = password = ''
   if not local_username_allowed(config, username):
      print('Anmeldung fehlgeschlagen: Benutzername entspricht nicht dem lokalen Anmeldenamen.')
      return 1
   status, response = login(config, device_id, username, password)
   if status != 200:
      print('Anmeldung fehlgeschlagen:', response.get('error', response))
      return 1
   save_store(user_file, data_root, username, password)
   print('Angemeldet als', username)
   generation, caps = user_capabilities(feature_root)
   print('Lokaler Fähigkeits-Stack Generation', generation)
   for index, cap in enumerate(caps, 1):
      print('%2d. %s' % (index, cap.get('title', cap['id'])))
   return 0


def run_gui(config, device_id, user_file, feature_root, data_root):
   import tkinter as tk
   from tkinter import messagebox

   root = tk.Tk()
   root.title('LCS Benutzer')
   root.minsize(470, 300)

   data_dir, saved_profile = load_store(user_file, data_root)
   scheduled = {}
   session = {'token': None, 'username': '', 'generation': None, 'running': True,
              'password': '', 'scheduled': scheduled, 'started': set(), 'tray': None}

   login_frame = tk.Frame(root, padx=18, pady=18)
   login_frame.pack(fill='both', expand=True)
   tk.Label(login_frame, text='LCS Benutzeranmeldung', font=('', 16, 'bold')).pack(anchor='w', pady=(0, 14))

   form = tk.Frame(login_frame)
   form.pack(fill='x')
   tk.Label(form, text='Benutzername', width=14, anchor='w').grid(row=0, column=0, pady=5)
   username = tk.Entry(form, width=32)
   username.grid(row=0, column=1, pady=5, sticky='ew')
   username.insert(0, saved_profile.get('username', ''))
   tk.Label(form, text='Passwort', width=14, anchor='w').grid(row=1, column=0, pady=5)
   password = tk.Entry(form, width=32, show='*')
   password.grid(row=1, column=1, pady=5, sticky='ew')
   tk.Label(form, text='Wiederholen (neu)', width=14, anchor='w').grid(row=2, column=0, pady=5)
   password_confirmation = tk.Entry(form, width=32, show='*')
   password_confirmation.grid(row=2, column=1, pady=5, sticky='ew')

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
   switch_button = tk.Button(menu_frame, text='Benutzer wechseln')
   switch_button.pack(anchor='e', pady=(6, 0))

   def heartbeat_loop():
      while session['running'] and session['token']:
         try:
            session_heartbeat(config, session['token'])
         except Exception:
            pass
         time.sleep(30)

   def action_password(cap):
      if not cap.get('requires_password'):
         return ''
      return session['password']

   def execute_capability(cap, parameters=None, action_id=None, notify=True):
      secret = action_password(cap)
      if cap.get('requires_password') and secret is None:
         return
      def worker():
         try:
            context = {'data_path': str(data_dir), 'username': session['username'], 'password': secret or ''}
            result = run_capability(cap, parameters or {}, timeout=int(cap.get('timeout', 120)), context=context)
            report_result(config, session['token'], cap['id'], result, action_id)
            if notify:
               root.after(0, lambda: messagebox.showinfo(cap.get('title', cap['id']), 'Aktion abgeschlossen.'))
         except Exception as exc:
            message = str(exc)
            if notify:
               root.after(0, lambda message=message: messagebox.showerror(cap.get('title', cap['id']), message))
      threading.Thread(target=worker, daemon=True).start()

   def run_automatic_and_disposable():
      if not session['token']:
         return
      now = int(time.time())
      _generation, caps = user_capabilities(feature_root)
      by_id = {cap['id']: cap for cap in caps}
      for cap in caps:
         for trigger in cap.get('triggers', []):
            kind = trigger.get('type')
            key = cap['id'] + ':' + json.dumps(trigger, sort_keys=True)
            previous = session['scheduled'].get(key, 0)
            today = time.strftime('%Y-%m-%d', time.localtime(now))
            due = ((kind == 'startup' and key not in session['started']) or
                   (kind == 'interval' and now - int(previous or 0) >= max(1, int(trigger.get('seconds', 3600)))) or
                   (kind == 'daily' and time.strftime('%H:%M', time.localtime(now)) >= trigger.get('at', '00:00')
                    and previous != today))
            if due:
               session['started'].add(key)
               session['scheduled'][key] = today if kind == 'daily' else now
               scheduler_path = data_dir / 'state' / 'scheduler.json'
               scheduler_path.write_text(json.dumps(session['scheduled'], indent=2), encoding='utf-8')
               execute_capability(cap, trigger.get('parameters', {}), notify=False)
      try:
         status, response = poll_user_actions(config, session['token'])
         if status == 200:
            for action in response.get('actions', []):
               cap = by_id.get(action['capability_id'])
               if cap:
                  execute_capability(cap, action.get('parameters', {}), action['id'], notify=False)
      except Exception:
         pass
      root.after(10000, run_automatic_and_disposable)

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
         return False
      creating_store = not (store_path(data_root, name) / 'credentials.json').is_file()
      if creating_store and secret != password_confirmation.get():
         messagebox.showerror('Anmeldung', 'Die Passwörter stimmen nicht überein. Bitte erneut eingeben.')
         username.delete(0, tk.END)
         password.delete(0, tk.END)
         password_confirmation.delete(0, tk.END)
         return False
      if not local_username_allowed(config, name):
         messagebox.showerror('Anmeldung', 'Der Benutzername entspricht nicht dem lokalen Anmeldenamen.')
         return False
      status_label.configure(text='Anmeldung läuft …')
      root.update_idletasks()
      try:
         code, response = login(config, device_id, name, secret)
      except Exception as exc:
         status_label.configure(text='')
         messagebox.showerror('Anmeldung', 'Server nicht erreichbar: %s' % exc)
         return False
      if code != 200:
         status_label.configure(text='')
         messagebox.showerror('Anmeldung', response.get('error', 'Anmeldung fehlgeschlagen.'))
         return False
      nonlocal data_dir
      data_dir = save_store(user_file, data_root, name, secret)
      try:
         session['scheduled'] = json.loads((data_dir / 'state' / 'scheduler.json').read_text(encoding='utf-8'))
      except Exception:
         session['scheduled'] = {}
      session['token'] = response['session_token']
      session['username'] = name
      session['password'] = secret
      password.delete(0, tk.END)
      password_confirmation.delete(0, tk.END)
      login_frame.pack_forget()
      header.configure(text='Angemeldet als ' + (response.get('full_name') or name))
      menu_frame.pack(fill='both', expand=True)
      load_menu(force=True)
      threading.Thread(target=heartbeat_loop, daemon=True).start()
      root.after(0, run_automatic_and_disposable)
      return True

   def show_window():
      root.deiconify()
      root.lift()

   def switch_user():
      session['token'] = None
      session['password'] = ''
      session['generation'] = None
      session['started'].clear()
      menu_frame.pack_forget()
      username.delete(0, tk.END)
      password.delete(0, tk.END)
      password_confirmation.delete(0, tk.END)
      login_frame.pack(fill='both', expand=True)
      show_window()

   def start_tray():
      try:
         import pystray
         from PIL import Image, ImageDraw
         image = Image.new('RGB', (64, 64), '#245c8a')
         ImageDraw.Draw(image).text((17, 20), 'LCS', fill='white')
         session['tray'] = pystray.Icon('lcs-client', image, 'LCS Client', pystray.Menu(
            pystray.MenuItem('Öffnen', lambda: root.after(0, show_window), default=True),
            pystray.MenuItem('Beenden', lambda: root.after(0, close))))
         session['tray'].run_detached()
      except Exception as exc:
         print('Tray-Symbol nicht verfügbar:', exc)

   login_button.configure(command=do_login)
   refresh_button.configure(command=lambda: load_menu(force=True))
   switch_button.configure(command=switch_user)
   password.bind('<Return>', lambda _event: do_login())
   root.after(5000, check_stack_change)

   def close():
      session['running'] = False
      session['token'] = None
      if session['tray']:
         session['tray'].stop()
      root.destroy()

   root.protocol('WM_DELETE_WINDOW', root.withdraw)
   start_tray()
   if saved_profile:
      username.delete(0, tk.END)
      username.insert(0, saved_profile['username'])
      password.insert(0, saved_profile['password'])
      if do_login() and '--show' not in sys.argv:
         root.withdraw()
   root.mainloop()
   return 0


def main():
   env_path = config_path()
   config = load_env(env_path)
   device_path, user_file, default_feature_root, data_root = runtime_paths(config)
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
      return run_cli(config, device_id, user_file, feature_root, data_root)
   try:
      return run_gui(config, device_id, user_file, feature_root, data_root)
   except Exception as exc:
      print('GUI nicht verfügbar:', exc)
      return run_cli(config, device_id, user_file, feature_root, data_root)


if __name__ == '__main__':
   raise SystemExit(main())
