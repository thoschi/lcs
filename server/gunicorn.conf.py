import os

bind = '%s:%s' % (
   os.environ.get('LCS_SERVER_HOST', '127.0.0.1'),
   os.environ.get('LCS_SERVER_PORT', '5000'),
)
workers = 1
threads = int(os.environ.get('LCS_SERVER_THREADS', '8'))
timeout = int(os.environ.get('LCS_SERVER_TIMEOUT', '120'))
max_requests = int(os.environ.get('LCS_SERVER_MAX_REQUESTS', '1000'))
max_requests_jitter = 100
