import base64
from pathlib import Path

MAX_READ_BYTES = 1024 * 1024


def run(context):
   p = context['parameters']
   operation = p.get('operation', 'read')
   path = Path(str(p.get('path', '')))
   if not path.is_absolute():
      raise ValueError('Nur absolute Dateipfade sind erlaubt')
   if operation == 'read':
      data = path.read_bytes()
      if len(data) > MAX_READ_BYTES:
         raise ValueError('Datei ist größer als 1 MiB')
      try:
         return {'path': str(path), 'content': data.decode('utf-8'), 'encoding': 'text'}
      except UnicodeDecodeError:
         return {'path': str(path), 'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64'}
   if operation == 'delete':
      path.unlink(missing_ok=True)
      return {'path': str(path), 'deleted': True}
   if operation in ('write', 'upload'):
      data = str(p.get('content', '')).encode('utf-8')
      if p.get('encoding') == 'base64':
         data = base64.b64decode(p.get('content', ''), validate=True)
      path.parent.mkdir(parents=True, exist_ok=True)
      temporary = path.with_name(path.name + '.lcs-tmp')
      temporary.write_bytes(data)
      temporary.replace(path)
      return {'path': str(path), 'written': len(data)}
   raise ValueError('Unbekannte Operation: ' + str(operation))
