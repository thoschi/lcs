"""Minimal login helper for the one-time local account setup."""
import logging
import os
import sys
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from common.config import load_env
from common.service_client import request


LOGGER = logging.getLogger('lcs.userservice')


def log_path():
   configured = os.environ.get('LCS_USER_SERVICE_LOG')
   if configured:
      return Path(configured).expanduser()
   if os.name == 'nt':
      root = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
   else:
      root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state'))
   return root / 'LCS' / 'user_service.log'


def configure_logging():
   LOGGER.setLevel(logging.DEBUG)
   formatter = logging.Formatter('%(asctime)s %(levelname)s pid=%(process)d thread=%(threadName)s %(message)s')
   stream = logging.StreamHandler()
   stream.setFormatter(formatter)
   LOGGER.addHandler(stream)
   path = log_path()
   try:
      path.parent.mkdir(parents=True, exist_ok=True)
      output = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
      output.setFormatter(formatter)
      LOGGER.addHandler(output)
   except Exception:
      LOGGER.exception('Logdatei konnte nicht geöffnet werden: %s', path)
   return path


def config_path():
   default = str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'client.env') if os.name == 'nt' else '/opt/lcs-service/client.env'
   return Path(os.environ.get('LCS_CONFIG', default))


def run_once(config):
   attempt = 0
   LOGGER.debug('Warte auf eine Einrichtungsanforderung des Systemdienstes')
   while True:
      attempt += 1
      try:
         LOGGER.debug('Statusabfrage %d wird gesendet', attempt)
         status = request(config, 'status')
         LOGGER.debug('Statusantwort %d empfangen: %r', attempt, status)
         # Beim Windows-Login kann der Helfer den Dienst erreichen, bevor dieser
         # die geklonte Geräteidentität geprüft und neu registriert hat.
         if (status.get('runtime_ready', True) and status.get('client_enabled')
               and status.get('initialization_required')):
            LOGGER.info('Einrichtungsanforderung erkannt')
            break
         LOGGER.debug('Noch keine Einrichtung erforderlich; nächste Abfrage in 2 Sekunden')
      except Exception:
         LOGGER.exception('Statusabfrage %d fehlgeschlagen; neuer Versuch in 2 Sekunden', attempt)
      time.sleep(2)
   # Linux can restore a saved shadow record without asking the user anything.
   if status.get('profile_exists') and not status.get('password_required'):
      LOGGER.info('Gespeichertes Profil wird ohne Dialog wiederhergestellt')
      result = request(config, 'initialize')
      LOGGER.debug('Antwort der Profilwiederherstellung: %r', result)
      if result.get('ok'):
         LOGGER.info('Profil wiederhergestellt; Abmeldung wird angefordert')
         request(config, 'execute', capability_id='logout')
         return 0
      LOGGER.error('Profilwiederherstellung fehlgeschlagen: %s', result.get('error', 'unbekannter Fehler'))
   LOGGER.debug('Einrichtungsdialog wird erstellt')
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
      LOGGER.info('Einrichtung wurde im Dialog bestätigt; Benutzername=%r, Domänenmodus=%s',
                  username.get().strip(), bool(domain_user))
      result = request(config, 'initialize', username=username.get().strip(), password=password.get())
      password.delete(0, tk.END)
      LOGGER.debug('Antwort der Benutzereinrichtung: %r', result)
      if not result.get('ok'):
         LOGGER.error('Benutzereinrichtung fehlgeschlagen: %s', result.get('error', 'unbekannter Fehler'))
         messagebox.showerror(root.title(), result.get('error', 'Einrichtung fehlgeschlagen.'))
         return
      LOGGER.info('Benutzereinrichtung erfolgreich')
      root.withdraw()
      try:
         if not domain_user:
            LOGGER.info('Abmeldung wird angefordert')
            request(config, 'execute', capability_id='logout')
      finally:
         root.destroy()

   button_text = 'LCS einrichten' if domain_user else 'Lokales Konto einrichten'
   button = tk.Button(frame, text=button_text, command=submit)
   button.grid(row=password_row + 1, column=0, columnspan=2, sticky='e', pady=(14, 0))
   password.bind('<Return>', lambda _event: submit())

   def hide_dialog():
      LOGGER.debug('Schließen des Dialogs angefordert; Dialog wird minimiert')
      root.iconify()

   root.protocol('WM_DELETE_WINDOW', hide_dialog)
   LOGGER.info('Einrichtungsdialog wird angezeigt; Profil vorhanden=%s, Domänenmodus=%s',
               bool(existing), bool(domain_user))
   root.mainloop()
   LOGGER.debug('Einrichtungsdialog wurde beendet')
   return 0


def main():
   path = config_path()
   output = configure_logging()
   LOGGER.info('LCS-Nutzerdienst gestartet; Plattform=%s, Python=%s, Konfiguration=%s, Logdatei=%s',
               sys.platform, sys.version.replace('\n', ' '), path, output)
   config = load_env(path)
   LOGGER.debug('Konfiguration geladen; Schlüssel=%s, Socket=%s', sorted(config),
                config.get('LCS_USER_SOCKET', r'\\.\pipe\lcs-user' if os.name == 'nt' else '/run/lcs/user.sock'))
   if os.name != 'nt':
      return run_once(config)
   while True:
      LOGGER.debug('Windows-Warteschleife wird gestartet')
      run_once(config)
      LOGGER.debug('Einrichtungsdurchlauf beendet; warte erneut auf Anforderungen')


if __name__ == '__main__':
   try:
      raise SystemExit(main())
   except Exception:
      LOGGER.exception('LCS-Nutzerdienst wurde unerwartet beendet')
      raise
