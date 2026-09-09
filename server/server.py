import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import core

HOST = os.environ.get('LCS_SERVER_HOST', '127.0.0.1')
PORT = int(os.environ.get('LCS_SERVER_PORT', '5000'))
BASE = Path(__file__).resolve().parent
RELEASES = Path(os.environ.get('LCS_RELEASES_DIR', str(BASE / 'releases')))
MANIFEST = Path(os.environ.get('LCS_MANIFEST_FILE', str(BASE / 'bootstrap-manifest.json')))
TOKEN_FILE = Path(os.environ.get('LCS_TOKEN_FILE', str(BASE / '.token')))


def bearer(headers):
   value = headers.get('Authorization', '')
   return value[7:] if value.startswith('Bearer ') else ''


def enrollment_token():
   try:
      return TOKEN_FILE.read_text(encoding='utf-8').strip()
   except Exception:
      return ''


def load_manifest_for_device(device):
   payload = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'generation': 0, 'capabilities': []}
   selected = []
   for cap in payload.get('capabilities', []):
      if core.capability_enabled_for_device(device['id'], cap['id']):
         selected.append(cap)
   return {'generation': payload.get('generation', 0), 'capabilities': selected}


class Handler(BaseHTTPRequestHandler):
   server_version = 'LCSServer/0.5'

   def log_message(self, fmt, *args):
      print('%s - %s' % (self.address_string(), fmt % args), flush=True)

   def send_json(self, status, payload):
      data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
      self.send_response(status)
      self.send_header('Content-Type', 'application/json; charset=utf-8')
      self.send_header('Content-Length', str(len(data)))
      self.end_headers()
      self.wfile.write(data)

   def send_file(self, path):
      data = path.read_bytes()
      self.send_response(200)
      self.send_header('Content-Type', 'application/zip')
      self.send_header('Content-Length', str(len(data)))
      self.end_headers()
      self.wfile.write(data)

   def read_json(self):
      length = int(self.headers.get('Content-Length', '0'))
      raw = self.rfile.read(length) if length else b'{}'
      try:
         return json.loads(raw.decode('utf-8'))
      except Exception:
         return {}

   def device(self):
      return core.authenticate_device(self.headers.get('X-Device-ID', ''), bearer(self.headers))

   def do_GET(self):
      path = urlparse(self.path).path
      if path == '/health':
         return self.send_json(200, {'ok': True, 'version': '0.5'})
      if path == '/api/v1/bootstrap/manifest':
         device = self.device()
         if not device:
            return self.send_json(401, {'error': 'unauthorized'})
         return self.send_json(200, load_manifest_for_device(device))
      if path.startswith('/api/v1/bootstrap/package/'):
         device = self.device()
         if not device:
            return self.send_json(401, {'error': 'unauthorized'})
         filename = unquote(path.rsplit('/', 1)[-1])
         if '/' in filename or '\\' in filename or filename.startswith('.'):
            return self.send_json(400, {'error': 'invalid filename'})
         allowed = {cap.get('filename') for cap in load_manifest_for_device(device).get('capabilities', [])}
         if filename not in allowed:
            return self.send_json(403, {'error': 'package not assigned to device'})
         target = RELEASES / filename
         if not target.is_file():
            return self.send_json(404, {'error': 'package not found'})
         return self.send_file(target)
      if path == '/api/v1/agent/poll':
         status, result = core.poll_actions(self.headers.get('X-Device-ID', ''), bearer(self.headers))
         return self.send_json(status, result)
      return self.send_json(404, {'error': 'not found'})

   def do_POST(self):
      path = urlparse(self.path).path
      payload = self.read_json()
      if path == '/api/v1/enroll':
         status, result = core.enroll(payload, enrollment_token())
      elif path == '/api/v1/heartbeat':
         status, result = core.heartbeat(self.headers.get('X-Device-ID', ''), bearer(self.headers), payload)
      elif path == '/api/v1/action/result':
         status, result = core.action_result(self.headers.get('X-Device-ID', ''), bearer(self.headers), payload)
      elif path == '/api/v1/event':
         status, result = core.device_event(self.headers.get('X-Device-ID', ''), bearer(self.headers), payload)
      elif path == '/api/v1/device/self-delete':
         status, result = core.self_delete(self.headers.get('X-Device-ID', ''), bearer(self.headers))
      elif path == '/api/v1/user/login':
         status, result = core.user_login(payload)
      elif path == '/api/v1/user/heartbeat':
         status, result = core.user_heartbeat(bearer(self.headers))
      elif path == '/api/v1/user/action/result':
         status, result = core.user_action_result(bearer(self.headers), payload)
      else:
         status, result = 404, {'error': 'not found'}
      self.send_json(status, result)


def main():
   core.init_db()
   RELEASES.mkdir(parents=True, exist_ok=True)
   if not enrollment_token():
      raise SystemExit('Enrollment token missing: %s' % TOKEN_FILE)
   server = ThreadingHTTPServer((HOST, PORT), Handler)
   print('LCS server listening on %s:%s' % (HOST, PORT), flush=True)
   server.serve_forever()


if __name__ == '__main__':
   main()
