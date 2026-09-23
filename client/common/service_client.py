"""Local IPC shared by the unprivileged LCS programs."""
import getpass
import json
import os
import socket
from multiprocessing.connection import Client


def request(config, operation, **payload):
   message = {'operation': operation, 'local_username': getpass.getuser(), **payload}
   if os.name == 'nt':
      with Client(config.get('LCS_USER_SOCKET', r'\\.\pipe\lcs-user'), family='AF_PIPE', authkey=None) as connection:
         connection.send_bytes(json.dumps(message, ensure_ascii=False).encode())
         return json.loads(connection.recv_bytes().decode())
   with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
      connection.settimeout(180)
      connection.connect(config.get('LCS_USER_SOCKET', '/run/lcs/user.sock'))
      connection.sendall(json.dumps(message, ensure_ascii=False).encode() + b'\n')
      data = b''
      while b'\n' not in data:
         chunk = connection.recv(65536)
         if not chunk:
            break
         data += chunk
   return json.loads(data.split(b'\n', 1)[0].decode())
