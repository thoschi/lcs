"""Small local menu for capabilities exposed by the installed system service."""
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from common.config import load_env
from common.service_client import request


def main():
   default = str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'client.env') if os.name == 'nt' else '/opt/lcs-service/client.env'
   config = load_env(Path(os.environ.get('LCS_CONFIG', default)))
   root = tk.Tk()
   root.title('LCS Benutzeraktionen')
   root.minsize(440, 180)
   body = tk.Frame(root, padx=18, pady=18)
   body.pack(fill='both', expand=True)
   tk.Label(body, text='Lokale Aktionen', font=('', 15, 'bold')).pack(anchor='w', pady=(0, 12))
   try:
      capabilities = request(config, 'capabilities').get('capabilities', [])
   except Exception as exc:
      messagebox.showerror(root.title(), 'Systemdienst nicht erreichbar: %s' % exc)
      return 2

   def execute(item):
      def worker():
         try:
            result = request(config, 'execute', capability_id=item['id'])
            if not result.get('ok'):
               root.after(0, lambda: messagebox.showerror(item['title'], result.get('error', 'Aktion fehlgeschlagen.')))
         except Exception as exc:
            root.after(0, lambda: messagebox.showerror(item['title'], str(exc)))
      threading.Thread(target=worker, daemon=True).start()

   def change_password():
      dialog = tk.Toplevel(root)
      dialog.title('Passwort ändern')
      frame = tk.Frame(dialog, padx=18, pady=18)
      frame.pack()
      entries = []
      for row, label in enumerate(('Altes Passwort', 'Neues Passwort', 'Neues Passwort wiederholen')):
         tk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=4)
         entry = tk.Entry(frame, show='*', width=30)
         entry.grid(row=row, column=1, pady=4)
         entries.append(entry)
      def submit():
         if entries[1].get() != entries[2].get():
            messagebox.showerror(dialog.title(), 'Die neuen Passwörter stimmen nicht überein.')
            return
         result = request(config, 'change_password', old_password=entries[0].get(), new_password=entries[1].get())
         if not result.get('ok'):
            messagebox.showerror(dialog.title(), result.get('error', 'Passwortänderung fehlgeschlagen.'))
            return
         dialog.destroy()
         request(config, 'execute', capability_id='logout')
      tk.Button(frame, text='Passwort ändern und abmelden', command=submit).grid(row=3, column=0, columnspan=2, sticky='e', pady=(12, 0))
      entries[0].focus_set()

   for item in capabilities:
      row = tk.Frame(body, pady=4)
      row.pack(fill='x')
      tk.Label(row, text=item['title'], anchor='w').pack(side='left', fill='x', expand=True)
      tk.Button(row, text='Ausf\u00fchren', command=lambda value=item: execute(value)).pack(side='right')
   tk.Button(body, text='Passwort ändern', command=change_password).pack(anchor='e', pady=(12, 0))
   if not capabilities:
      tk.Label(body, text='Keine lokal ausf\u00fchrbaren F\u00e4higkeiten installiert.').pack(anchor='w')
   root.mainloop()
   return 0


if __name__ == '__main__':
   raise SystemExit(main())
