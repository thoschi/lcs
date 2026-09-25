"""Minimal login helper for the one-time local account setup."""
import os
import sys
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from common.config import load_env
from common.service_client import request


def debug(message, **fields):
   details = ' '.join('%s=%r' % item for item in fields.items())
   line = '%s [lcs-userservice] DEBUG %s%s' % (
      time.strftime('%Y-%m-%dT%H:%M:%S%z'), message, (' ' + details) if details else '')
   print(line, flush=True)


def config_path():
   default = str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'client.env') if os.name == 'nt' else '/opt/lcs-service/client.env'
   return Path(os.environ.get('LCS_CONFIG', default))


def run_once(config):
   debug('Warte auf Initialisierungsanforderung')
   attempt = 0
   previous_status = None
   previous_error = None
   while True:
      attempt += 1
      try:
         if attempt == 1 or attempt % 30 == 0:
            debug('Statusabfrage wird gesendet', attempt=attempt)
         status = request(config, 'status')
         summary = (status.get('ok'), status.get('initialization_required'),
                    status.get('profile_exists'), status.get('password_required'))
         if summary != previous_status or attempt % 30 == 0:
            debug('Statusantwort empfangen', ok=summary[0], initialization_required=summary[1],
                  profile_exists=summary[2], password_required=summary[3])
         previous_status = summary
         previous_error = None
         if status.get('initialization_required'):
            break
      except Exception as exc:
         error = '%s: %s' % (type(exc).__name__, exc)
         if error != previous_error or attempt % 30 == 0:
            debug('Statusabfrage fehlgeschlagen', error=error)
         previous_error = error
      time.sleep(2)
   def initialize_and_logout(**payload):
      debug('Automatische Initialisierung wird angefordert')
      result = request(config, 'initialize', **payload)
      if not result.get('ok'):
         debug('Initialisierung fehlgeschlagen', error=result.get('error', 'Unbekannter Fehler'))
         return result
      debug('Initialisierung abgeschlossen; Abmeldung wird angefordert')
      logout = request(config, 'execute', capability_id='logout')
      return logout if not logout.get('ok') else result

   # Linux can restore a saved shadow record without asking the user anything.
   if os.name != 'nt' and status.get('domain_username') and not status.get('password_required'):
      debug('Domänenprofil wird ohne Dialog initialisiert')
      result = initialize_and_logout()
      return 0 if result.get('ok') else 1
   if status.get('profile_exists') and not status.get('password_required'):
      debug('Vorhandenes Profil wird ohne Dialog wiederhergestellt')
      result = initialize_and_logout()
      return 0 if result.get('ok') else 1
   debug('Passwortdialog wird geöffnet', username_known=bool(status.get('username_known')),
         domain_username=bool(status.get('domain_username')))
   root = tk.Tk()
   existing = status.get('profile_exists')
   username_known = status.get('username_known') or existing
   domain_user = status.get('domain_username')
   root.title('Schulnetz-Passwort eingeben' if username_known or domain_user else 'Schulnetz-Login und -Passwort eingeben')
   root.resizable(False, False)
   frame = tk.Frame(root, padx=22, pady=18)
   frame.pack()
   text = ('Geben Sie genau das Passwort Ihres Schulnetz-Zugangs ein.' if username_known or domain_user else
           'Geben Sie Ihren Schulnetz-Login und genau das zugeh\u00f6rige Schulnetz-Passwort ein.')
   tk.Label(frame, text=text, wraplength=410, justify='left').grid(row=0, column=0, columnspan=2, pady=(0, 14))
   username = tk.Entry(frame, width=32)
   if not domain_user:
      tk.Label(frame, text='Schulnetz-Login').grid(row=1, column=0, sticky='w', pady=4)
      username.grid(row=1, column=1, pady=4)
   password_row = 1 if domain_user else 2
   tk.Label(frame, text='Schulnetz-Passwort').grid(row=password_row, column=0, sticky='w', pady=4)
   password = tk.Entry(frame, width=32, show='*')
   password.grid(row=password_row, column=1, pady=4)
   if username_known or domain_user:
      username.insert(0, status.get('username', ''))
      username.configure(state='disabled')

   def submit():
      debug('Initialisierung aus dem Dialog wird angefordert', username_entered=bool(username.get().strip()))
      result = request(config, 'initialize', username=username.get().strip(), password=password.get())
      password.delete(0, tk.END)
      if not result.get('ok'):
         messagebox.showerror(root.title(), result.get('error', 'Einrichtung fehlgeschlagen.'))
         return
      root.withdraw()
      debug('Initialisierung abgeschlossen; Abmeldung wird angefordert')
      result = request(config, 'execute', capability_id='logout')
      if not result.get('ok'):
         root.deiconify()
         messagebox.showerror(root.title(), result.get('error', 'Abmeldung fehlgeschlagen.'))
         return
      root.destroy()

   button_text = 'LCS einrichten' if domain_user else 'Lokales Konto einrichten'
   button = tk.Button(frame, text=button_text, command=submit)
   button.grid(row=password_row + 1, column=0, columnspan=2, sticky='e', pady=(14, 0))
   password.bind('<Return>', lambda _event: submit())
   root.protocol('WM_DELETE_WINDOW', root.iconify)
   root.mainloop()
   return 0


def main():
   path = config_path()
   debug('Nutzerservice gestartet', config=str(path), platform=os.name)
   config = load_env(path)
   if os.name != 'nt':
      return run_once(config)
   while True:
      run_once(config)


if __name__ == '__main__':
   try:
      raise SystemExit(main())
   except Exception as exc:
      debug('Nutzerservice unerwartet beendet', error='%s: %s' % (type(exc).__name__, exc))
      traceback.print_exc(file=sys.stdout)
      raise
