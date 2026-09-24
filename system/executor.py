"""Small non-blocking executor used by the LCS moderator."""
import queue
import threading
import uuid


class Executor:
   def __init__(self, name, handler):
      self.name = name
      self.handler = handler
      self.jobs = queue.Queue()
      self.results = {}
      self.lock = threading.Lock()
      threading.Thread(target=self._run, name='lcs-' + name, daemon=True).start()

   def submit(self, payload):
      job_id = uuid.uuid4().hex
      self.jobs.put((job_id, payload))
      return job_id

   def take(self, job_id):
      with self.lock:
         return self.results.pop(job_id, None)

   def _run(self):
      while True:
         job_id, payload = self.jobs.get()
         try:
            result = {'ok': True, 'result': self.handler(payload)}
         except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
         with self.lock:
            self.results[job_id] = result
         self.jobs.task_done()
