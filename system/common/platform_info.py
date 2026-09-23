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


def hostname():
   return socket.gethostname()


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


def _linux_os():
   values = {}
   try:
      for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
         if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip().strip('"')
   except OSError:
      pass
   return values.get('PRETTY_NAME') or platform.platform()


def _windows_value(command):
   return _cmd(['powershell', '-NoProfile', '-NonInteractive', '-Command', command])


def system_information(service_version):
   """Collect only bounded, local inventory calls suitable for every heartbeat."""
   addresses = []
   try:
      addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname(), None)
                          if item[0] in (socket.AF_INET, socket.AF_INET6) and not item[4][0].startswith('127.')})
   except OSError:
      pass
   mac_value = uuid.getnode()
   mac = ':'.join('%02x' % ((mac_value >> shift) & 0xff) for shift in range(40, -1, -8))
   if os.name == 'nt':
      os_version = _windows_value('(Get-CimInstance Win32_OperatingSystem).Caption + " " + (Get-CimInstance Win32_OperatingSystem).Version') or platform.platform()
      serial = _windows_value('(Get-CimInstance Win32_BIOS).SerialNumber')
      exam_mode = bool(_cmd(['sc.exe', 'query', 'squid'])) and 'RUNNING' in _cmd(['sc.exe', 'query', 'squid'])
   else:
      os_version = _linux_os()
      try:
         serial = Path('/sys/class/dmi/id/product_serial').read_text(encoding='utf-8').strip()
      except OSError:
         serial = ''
      exam_mode = _cmd(['systemctl', 'is-active', 'squid']) == 'active'
   users = logged_in_users()
   return {
      'service_version': service_version,
      'capabilities': [],
      'hostname': hostname(),
      'mac': mac,
      'ip_addresses': addresses,
      'os': os_version,
      'exam_mode': exam_mode,
      'current_users': users,
      'current_user': ', '.join(users) if users else 'niemand angemeldet',
      'serial_number': serial,
      'architecture': platform.machine(),
   }
