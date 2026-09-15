import os
import socket
import subprocess


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
