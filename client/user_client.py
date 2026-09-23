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

   for item in capabilities:
      row = tk.Frame(body, pady=4)
      row.pack(fill='x')
      tk.Label(row, text=item['title'], anchor='w').pack(side='left', fill='x', expand=True)
      tk.Button(row, text='Ausf\u00fchren', command=lambda value=item: execute(value)).pack(side='right')
   if not capabilities:
      tk.Label(body, text='Keine lokal ausf\u00fchrbaren F\u00e4higkeiten installiert.').pack(anchor='w')
   root.mainloop()
   return 0


if __name__ == '__main__':
   raise SystemExit(main())
