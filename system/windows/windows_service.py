import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import win32event
import win32service
import win32serviceutil
import servicemanager

class LCSService(win32serviceutil.ServiceFramework):
   _svc_name_ = 'LCSService'
   _svc_display_name_ = 'LCS System Service'
   _svc_description_ = 'Registers the device and reports status to the LCS management server.'
   _exe_name_ = str(Path(sys.prefix) / 'pythonservice.exe')

   def __init__(self, args):
      super().__init__(args)
      self.stop_event = win32event.CreateEvent(None, 0, 0, None)

   def SvcStop(self):
      self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
      win32event.SetEvent(self.stop_event)

   def SvcDoRun(self):
      servicemanager.LogInfoMsg('LCSService starting')
      def stopped():
         return win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0
      log_path = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'LCS' / 'service.log'
      log_path.parent.mkdir(parents=True, exist_ok=True)
      with log_path.open('a', encoding='utf-8', buffering=1) as log:
         with redirect_stdout(log), redirect_stderr(log):
            try:
               from bootstrap import run
               run(os.environ.get('LCS_CONFIG'), stop_requested=stopped)
            except Exception:
               traceback.print_exc()
               servicemanager.LogErrorMsg('LCSService stopped unexpectedly; see ' + str(log_path))
               raise


if __name__ == '__main__':
   win32serviceutil.HandleCommandLine(LCSService)
