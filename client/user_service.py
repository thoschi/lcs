"""Minimal login helper for the one-time local account setup."""
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from common.config import load_env
from common.service_client import request


def config_path():
   default = str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'client.env') if os.name == 'nt' else '/opt/lcs-service/client.env'
   return Path(os.environ.get('LCS_CONFIG', default))


def run_once(config):
   while True:
      try:
         status = request(config, 'status')
         # Beim Windows-Login kann der Helfer den Dienst erreichen, bevor dieser
         # die geklonte Geräteidentität geprüft und neu registriert hat.
         if (status.get('runtime_ready', True) and status.get('client_enabled')
               and status.get('initialization_required')):
            break
      except Exception:
         pass
      time.sleep(2)
   # Linux can restore a saved shadow record without asking the user anything.
   if status.get('profile_exists') and not status.get('password_required'):
      result = request(config, 'initialize')
      if result.get('ok'):
         request(config, 'execute', capability_id='logout')
         return 0
   root = tk.Tk()
   existing = status.get('profile_exists')
   domain_user = status.get('domain_username')
   root.title('Schulnetz-Passwort eingeben' if existing or domain_user else 'Schulnetz-Login und -Passwort eingeben')
   root.resizable(False, False)
   frame = tk.Frame(root, padx=22, pady=18)
   frame.pack()
   text = ('Geben Sie genau das Passwort Ihres Schulnetz-Zugangs ein.' if existing or domain_user else
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
   if existing or domain_user:
      username.insert(0, status.get('username', ''))
      username.configure(state='disabled')

   def submit():
      result = request(config, 'initialize', username=username.get().strip(), password=password.get())
      password.delete(0, tk.END)
      if not result.get('ok'):
         messagebox.showerror(root.title(), result.get('error', 'Einrichtung fehlgeschlagen.'))
         return
      root.withdraw()
      try:
         if not domain_user:
            request(config, 'execute', capability_id='logout')
      finally:
         root.destroy()

   button_text = 'LCS einrichten' if domain_user else 'Lokales Konto einrichten'
   button = tk.Button(frame, text=button_text, command=submit)
   button.grid(row=password_row + 1, column=0, columnspan=2, sticky='e', pady=(14, 0))
   password.bind('<Return>', lambda _event: submit())
   root.protocol('WM_DELETE_WINDOW', root.iconify)
   root.mainloop()
   return 0


def main():
   config = load_env(config_path())
   if os.name != 'nt':
      return run_once(config)
   while True:
      run_once(config)


if __name__ == '__main__':
   raise SystemExit(main())
