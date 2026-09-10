import getpass
import json
import os
import platform
import socket
import subprocess
import uuid
from pathlib import Path


def _cmd(args):
   try:
      return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=4).strip()
   except Exception:
      return ''


def machine_id():
   if os.name == 'nt':
      out = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_ComputerSystemProduct).UUID"])
      if out:
         return out.strip()
   for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
      p = Path(path)
      if p.exists():
         value = p.read_text(encoding='utf-8', errors='ignore').strip()
         if value:
            return value
   return str(uuid.getnode())


def hardware_info():
   info = {
      'hostname': socket.gethostname(),
      'platform': platform.system().lower(),
      'platform_release': platform.release(),
      'architecture': platform.machine(),
      'machine_id': machine_id(),
   }
   if os.name == 'nt':
      info['manufacturer'] = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_ComputerSystem).Manufacturer"])
      info['model'] = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_ComputerSystem).Model"])
      info['bios'] = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion"])
      info['serial_number'] = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_BIOS).SerialNumber"])
      info['mac_addresses'] = [value for value in _cmd(['powershell', '-NoProfile', '-Command',
         "(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True').MACAddress"]).splitlines() if value]
      info['memory_bytes'] = _cmd(['powershell', '-NoProfile', '-Command', "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
   else:
      def read_dmi(name):
         p = Path('/sys/class/dmi/id') / name
         return p.read_text(encoding='utf-8', errors='ignore').strip() if p.exists() else ''
      info['manufacturer'] = read_dmi('sys_vendor')
      info['model'] = read_dmi('product_name')
      info['bios'] = read_dmi('bios_version')
      info['serial_number'] = read_dmi('product_serial')
      info['mac_addresses'] = sorted({address.read_text().strip() for address in Path('/sys/class/net').glob('*/address')
                                      if address.is_file() and address.read_text().strip() != '00:00:00:00:00:00'})
      try:
         for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
               info['memory_bytes'] = int(line.split()[1]) * 1024
               break
      except Exception:
         pass
   return info


def logged_in_users():
   users = []
   if os.name == 'nt':
      output = _cmd(['query', 'user'])
      for line in output.splitlines()[1:]:
         parts = line.replace('>', ' ').split()
         if parts:
            users.append(parts[0])
   else:
      output = _cmd(['loginctl', 'list-users', '--no-legend'])
      for line in output.splitlines():
         parts = line.split()
         if len(parts) >= 2:
            users.append(parts[1])
      if not users:
         output = _cmd(['who'])
         for line in output.splitlines():
            if line.split():
               users.append(line.split()[0])
   return sorted(set(users))
