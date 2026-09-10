from pathlib import Path


def run(context):
   root = Path(context['data_path'])
   for name in ('backgrounds', 'printers', 'state'):
      (root / name).mkdir(parents=True, exist_ok=True)
   return {
      'data_path': str(root),
      'backgrounds': len(list((root / 'backgrounds').iterdir())),
      'printers': len(list((root / 'printers').iterdir())),
   }
