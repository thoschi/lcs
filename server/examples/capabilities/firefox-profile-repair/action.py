from pathlib import Path


def run(context):
   removed = []
   home = Path(context['user_home'])
   for profile in (home / '.mozilla' / 'firefox').glob('*'):
      if not profile.is_dir():
         continue
      for name in ('lock', '.parentlock', 'parent.lock'):
         target = profile / name
         if target.exists() or target.is_symlink():
            target.unlink()
            removed.append(str(target))
   return {'removed': removed}
