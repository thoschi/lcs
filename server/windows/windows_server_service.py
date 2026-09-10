import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import servicemanager
import win32service
import win32serviceutil


def load_env(path):
   for raw in Path(path).read_text(encoding='utf-8').splitlines():
      line = raw.strip()
      if line and not line.startswith('#') and '=' in line:
         key, value = line.split('=', 1)
         os.environ[key.strip()] = value.strip().strip('"').strip("'")


class LCSServer(win32serviceutil.ServiceFramework):
   _svc_name_ = 'LCSServer'
   _svc_display_name_ = 'LCS Management Server'
   _svc_description_ = 'Provides the LCS management API and administration interface.'

   def SvcStop(self):
      self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
      os._exit(0)

   def SvcDoRun(self):
      servicemanager.LogInfoMsg('LCSServer starting')
      env_path = os.environ.get('LCS_SERVER_ENV', str(BASE / 'server.env'))
      load_env(env_path)
      from server import main
      main()


if __name__ == '__main__':
   win32serviceutil.HandleCommandLine(LCSServer)
