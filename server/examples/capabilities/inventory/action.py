import json
import os
import platform
import socket
import subprocess
import uuid
from pathlib import Path


def cmd(args):
   try:
      return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=30).strip()
   except Exception:
      return ''


def run(context):
   try:
      ip_addresses = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)
                             if item[4][0] not in ('127.0.0.1', '::1')})
   except OSError:
      ip_addresses = []
   mac = uuid.getnode()
   info = {
      'ip_addresses': ip_addresses,
      'mac_addresses': [':'.join('%02x' % ((mac >> shift) & 0xff) for shift in range(40, -1, -8))],
      'operating_system': platform.system(),
      'os_release': platform.release(),
      'architecture': platform.machine(),
      'processor': platform.processor(),
   }
   if os.name == 'nt':
      info['manufacturer'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_ComputerSystem).Manufacturer'])
      info['model'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_ComputerSystem).Model'])
      info['serial_number'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_BIOS).SerialNumber'])
      info['bios'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion'])
      info['memory_bytes'] = cmd(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory'])
      raw = cmd(['powershell', '-NoProfile', '-Command', 'Get-Package | Select-Object -ExpandProperty Name | Sort-Object -Unique | ConvertTo-Json -Compress'])
      try:
         info['software'] = json.loads(raw) if raw else []
      except ValueError:
         info['software'] = raw.splitlines()
   else:
      dmi = Path('/sys/class/dmi/id')
      read_dmi = lambda name: (dmi / name).read_text(errors='ignore').strip() if (dmi / name).exists() else ''
      info.update(manufacturer=read_dmi('sys_vendor'), model=read_dmi('product_name'),
                  serial_number=read_dmi('product_serial'), bios=read_dmi('bios_version'))
      memory = next((line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemTotal:')), '')
      info['memory_bytes'] = int(memory) * 1024 if memory else ''
      info['software'] = cmd(['dpkg-query', '-W', '-f=${binary:Package}\n']).splitlines()
   return {'client_info': info}
