import socket
import uuid


def run(context):
   addresses = []
   try:
      addresses = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)
                          if item[4][0] not in ('127.0.0.1', '::1')})
   except OSError:
      pass
   mac = uuid.getnode()
   return {'client_info': {
      'ip_addresses': addresses,
      'mac_addresses': [':'.join('%02x' % ((mac >> shift) & 0xff) for shift in range(40, -1, -8))],
   }}
