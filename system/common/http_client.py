import json
import ssl
import urllib.error
import urllib.request


def request_json(method, url, data=None, headers=None, ca_file=None, timeout=10):
   body = None
   req_headers = {'Content-Type': 'application/json'}
   if headers:
      req_headers.update(headers)
   if data is not None:
      body = json.dumps(data).encode('utf-8')
   req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
   context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
   try:
      with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
         raw = response.read().decode('utf-8')
         return response.status, json.loads(raw) if raw else {}
   except urllib.error.HTTPError as exc:
      raw = exc.read().decode('utf-8', errors='replace')
      try:
         payload = json.loads(raw) if raw else {}
      except Exception:
         payload = {'error': raw}
      return exc.code, payload
