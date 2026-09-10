import shutil
import time


def run(_context):
   total, used, free = shutil.disk_usage('/')
   return {'uptime_seconds': int(time.monotonic()), 'disk_total': total,
           'disk_used': used, 'disk_free': free}
