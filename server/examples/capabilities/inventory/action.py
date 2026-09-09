import json
import os
import subprocess
import uuid
from pathlib import Path


def cmd(args):
   try:
      return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=30).strip()
   except Exception:
      return ''


def run(context):
   result = {'mac_addresses': []}
   mac = uuid.getnode()
   result['mac_addresses'].append(':'.join('%02x' % ((mac >> ele) & 0xff) for ele in range(40, -1, -8)))
   if os.name == 'nt':
      result['serial_number'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_BIOS).SerialNumber'])
      raw = cmd(['powershell', '-NoProfile', '-Command', 'Get-Package | Select-Object -ExpandProperty Name | Sort-Object -Unique | ConvertTo-Json -Compress'])
      try:
         result['software'] = json.loads(raw) if raw else []
      except Exception:
         result['software'] = raw.splitlines()
   else:
      serial = Path('/sys/class/dmi/id/product_serial')
      result['serial_number'] = serial.read_text(errors='ignore').strip() if serial.exists() else ''
      raw = cmd(['dpkg-query', '-W', '-f=${binary:Package}\n'])
      result['software'] = raw.splitlines() if raw else []
   return result
