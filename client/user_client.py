import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from common.config import load_env

VERSION = '0.6.0'


def config_path():
   default = (str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'client.env')
              if os.name == 'nt' else '/opt/lcs-service/client.env')
   return Path(os.environ.get('LCS_CONFIG', default))


def request_service(config, operation, **payload):
   default_socket = (str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'user.sock')
                     if os.name == 'nt' else '/run/lcs/user.sock')
   socket_path = config.get('LCS_USER_SOCKET', default_socket)
   request = {'operation': operation, 'client_version': VERSION, **payload}
   with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
      connection.settimeout(180)
      connection.connect(socket_path)
      connection.sendall(json.dumps(request, ensure_ascii=False).encode('utf-8') + b'\n')
      chunks = []
      while True:
         chunk = connection.recv(65536)
         if not chunk:
            break
         chunks.append(chunk)
         if b'\n' in chunk:
            break
   return json.loads(b''.join(chunks).split(b'\n', 1)[0].decode('utf-8'))


def run_cli(config):
   status = request_service(config, 'status')
   if not status.get('initialization_required'):
      print('System bereit für', status.get('username', 'den Benutzer'))
      return 0
   if not status.get('profile_exists'):
      import getpass
      username = input('Benutzername: ').strip()
      password = getpass.getpass('Passwort: ')
      if password != getpass.getpass('Passwort wiederholen: '):
         print('Die Passwörter stimmen nicht überein.')
         return 1
      result = request_service(config, 'initialize', username=username, password=password)
      if not result.get('ok'):
         print(result.get('error', 'Ersteinrichtung fehlgeschlagen.'))
         return 1
   elif status.get('password_required'):
      import getpass
      result = request_service(config, 'initialize', password=getpass.getpass('Passwort: '))
      if not result.get('ok'):
         print(result.get('error', 'Wiederherstellung fehlgeschlagen.'))
         return 1
   else:
      result = request_service(config, 'initialize')
      if not result.get('ok'):
         print(result.get('error', 'Wiederherstellung fehlgeschlagen.'))
         return 1
   print('System bereit für', result.get('username', 'den Benutzer'))
   return 0


def run_gui(config, status):
   import tkinter as tk
   from tkinter import messagebox

   root = tk.Tk()
   root.title('LCS Benutzer')
   root.minsize(470, 300)
   session = {'running': True, 'tray': None}

   setup_frame = tk.Frame(root, padx=18, pady=18)
   setup_frame.pack(fill='both', expand=True)
   tk.Label(setup_frame, text='Ersteinrichtung', font=('', 16, 'bold')).pack(anchor='w', pady=(0, 14))
   form = tk.Frame(setup_frame)
   form.pack(fill='x')
   tk.Label(form, text='Benutzername', width=18, anchor='w').grid(row=0, column=0, pady=5)
   username = tk.Entry(form, width=32)
   username.grid(row=0, column=1, pady=5, sticky='ew')
   tk.Label(form, text='Passwort', width=18, anchor='w').grid(row=1, column=0, pady=5)
   password = tk.Entry(form, width=32, show='*')
   password.grid(row=1, column=1, pady=5, sticky='ew')
   tk.Label(form, text='Passwort wiederholen', width=18, anchor='w').grid(row=2, column=0, pady=5)
   confirmation = tk.Entry(form, width=32, show='*')
   confirmation.grid(row=2, column=1, pady=5, sticky='ew')
   status_label = tk.Label(setup_frame, text='', anchor='w')
   status_label.pack(fill='x', pady=(10, 4))
   setup_button = tk.Button(setup_frame, text='Einrichten')
   setup_button.pack(anchor='e', pady=(5, 0))
   setup_exit = tk.Button(setup_frame, text='Beenden')
   setup_exit.pack(anchor='e', pady=(6, 0))

   menu_frame = tk.Frame(root, padx=18, pady=18)
   tk.Label(menu_frame, text='LCS Benutzeraktionen', font=('', 15, 'bold')).pack(anchor='w')
   options_frame = tk.Frame(menu_frame)
   options_frame.pack(fill='both', expand=True, pady=(12, 0))
   menu_exit = tk.Button(menu_frame, text='Beenden')
   menu_exit.pack(anchor='e', pady=(12, 0))

   def close():
      session['running'] = False
      if session['tray']:
         session['tray'].stop()
      root.destroy()

   def execute(capability_id, title):
      def worker():
         try:
            result = request_service(config, 'execute', capability_id=capability_id)
            if result.get('ok'):
               root.after(0, lambda: messagebox.showinfo(title, 'Aktion abgeschlossen.'))
            else:
               message = result.get('error', 'Aktion fehlgeschlagen.')
               root.after(0, lambda: messagebox.showerror(title, message))
         except Exception as exc:
            message = str(exc)
            root.after(0, lambda: messagebox.showerror(title, message))
      threading.Thread(target=worker, daemon=True).start()

   def load_menu():
      for widget in options_frame.winfo_children():
         widget.destroy()
      try:
         capabilities = request_service(config, 'capabilities').get('capabilities', [])
      except Exception as exc:
         tk.Label(options_frame, text='Systemdienst nicht erreichbar: %s' % exc, anchor='w').pack(fill='x')
         return
      if not capabilities:
         tk.Label(options_frame, text='Derzeit sind keine Benutzeraktionen verfügbar.', anchor='w').pack(fill='x')
      for cap in capabilities:
         row = tk.Frame(options_frame, pady=4)
         row.pack(fill='x')
         text = tk.Frame(row)
         text.pack(side='left', fill='x', expand=True)
         title = cap.get('title', cap['id'])
         tk.Label(text, text=title, font=('', 11, 'bold'), anchor='w').pack(fill='x')
         if cap.get('description'):
            tk.Label(text, text=cap['description'], anchor='w', justify='left', wraplength=300).pack(fill='x')
         tk.Button(row, text='Ausführen', command=lambda c=cap, t=title: execute(c['id'], t)).pack(side='right')

   def show_menu():
      setup_frame.pack_forget()
      menu_frame.pack(fill='both', expand=True)
      load_menu()
      root.deiconify()
      root.lift()

   def initialize_new():
      name = username.get().strip()
      secret = password.get()
      if not name or not secret:
         messagebox.showerror('Ersteinrichtung', 'Benutzername und Passwort sind erforderlich.')
         return
      if not status.get('profile_exists') and secret != confirmation.get():
         messagebox.showerror('Ersteinrichtung', 'Die Passwörter stimmen nicht überein.')
         password.delete(0, tk.END)
         confirmation.delete(0, tk.END)
         return
      status_label.configure(text='System wird eingerichtet …')
      root.update_idletasks()
      try:
         result = request_service(config, 'initialize', username=name, password=secret)
      except Exception as exc:
         result = {'error': str(exc)}
      password.delete(0, tk.END)
      confirmation.delete(0, tk.END)
      if not result.get('ok'):
         status_label.configure(text='')
         messagebox.showerror('Ersteinrichtung', result.get('error', 'Ersteinrichtung fehlgeschlagen.'))
         return
      root.withdraw()

   def start_tray():
      try:
         import pystray
         from PIL import Image, ImageDraw
         image = Image.new('RGB', (64, 64), '#245c8a')
         ImageDraw.Draw(image).text((17, 20), 'LCS', fill='white')
         session['tray'] = pystray.Icon('lcs-client', image, 'LCS Client', pystray.Menu(
            pystray.MenuItem('Öffnen', lambda: root.after(0, show_menu), default=True),
            pystray.MenuItem('Beenden', lambda: root.after(0, close))))
         session['tray'].run_detached()
      except Exception as exc:
         print('Tray-Symbol nicht verfügbar:', exc)

   setup_button.configure(command=initialize_new)
   setup_exit.configure(command=close)
   menu_exit.configure(command=close)
   password.bind('<Return>', lambda _event: initialize_new())
   root.protocol('WM_DELETE_WINDOW', root.withdraw)
   start_tray()

   if status.get('initialization_required') and status.get('profile_exists') and status.get('password_required'):
      username.insert(0, status.get('username', ''))
      username.configure(state='readonly')
      for widget in (form.grid_slaves(row=2)):
         widget.grid_remove()
   elif status.get('initialization_required') and status.get('profile_exists'):
      result = request_service(config, 'initialize')
      if not result.get('ok'):
         messagebox.showerror('Ersteinrichtung', result.get('error', 'Wiederherstellung fehlgeschlagen.'))
      elif '--show' in sys.argv:
         show_menu()
      else:
         root.withdraw()
   elif not status.get('initialization_required'):
      if '--show' in sys.argv:
         show_menu()
      else:
         root.withdraw()
   root.mainloop()
   return 0


def main():
   config = load_env(config_path())
   while True:
      try:
         status = request_service(config, 'status')
      except Exception:
         time.sleep(2)
         continue
      if status.get('client_enabled'):
         break
      if status.get('image_source'):
         print('Kein aktiver Nutzer-Client: Gerät ist noch nicht registriert oder dient als Image-Vorlage.')
         return 0
      time.sleep(2)
   try:
      if '--cli' in sys.argv:
         return run_cli(config)
      return run_gui(config, status)
   except Exception as exc:
      print('LCS-Systemdienst nicht erreichbar:', exc)
      return 2


if __name__ == '__main__':
   raise SystemExit(main())
