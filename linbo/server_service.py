"""Authenticated, deliberately small service for a LINBO server."""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get('LCS_LINBO_SERVER_TOKEN', '')
COMMANDS = {
   'reload_dhcp': ['systemctl', 'reload', 'isc-dhcp-server'],
   'restart_tftp': ['systemctl', 'restart', 'tftpd-hpa'],
}

class Handler(BaseHTTPRequestHandler):
   def reply(self, status, payload):
      data = json.dumps(payload).encode()
      self.send_response(status); self.send_header('Content-Type', 'application/json')
      self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

   def authorized(self):
      return TOKEN and self.headers.get('Authorization') == 'Bearer ' + TOKEN

   def do_GET(self):
      if self.path == '/health':
         return self.reply(200, {'ok': True, 'service': 'lcs-linbo-server', 'version': '0.8.0'})
      self.reply(404, {'error': 'not found'})

   def do_POST(self):
      if not self.authorized():
         return self.reply(401, {'error': 'unauthorized'})
      length = min(int(self.headers.get('Content-Length', '0')), 65536)
      payload = json.loads(self.rfile.read(length) or b'{}')
      if self.path != '/api/v1/action' or payload.get('action') not in COMMANDS:
         return self.reply(400, {'error': 'unknown action'})
      command = COMMANDS[payload['action']]
      result = subprocess.run(command, capture_output=True, text=True, timeout=30)
      self.reply(200 if result.returncode == 0 else 500,
                 {'ok': result.returncode == 0, 'output': (result.stdout or result.stderr)[-4096:]})

   def log_message(self, fmt, *args):
      print('[lcs-linbo-server] ' + fmt % args, flush=True)

if __name__ == '__main__':
   ThreadingHTTPServer((os.environ.get('LCS_LINBO_HOST', '127.0.0.1'),
                        int(os.environ.get('LCS_LINBO_PORT', '8091'))), Handler).serve_forever()
