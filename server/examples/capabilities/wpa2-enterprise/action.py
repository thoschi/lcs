import os
import subprocess
import tempfile
from xml.sax.saxutils import escape


def run(context):
   p = context['parameters']
   required = ('ssid', 'username', 'password')
   if any(not p.get(key) for key in required):
      raise ValueError('ssid, username und password sind erforderlich')
   if os.name != 'nt':
      command = ['nmcli', 'connection', 'add', 'type', 'wifi', 'con-name', p['ssid'], 'ssid', p['ssid'],
                 'wifi-sec.key-mgmt', 'wpa-eap', '802-1x.eap', 'peap', '802-1x.identity', p['username'],
                 '802-1x.password', p['password'], '802-1x.phase2-auth', 'mschapv2']
      if p.get('identity'):
         command += ['802-1x.anonymous-identity', p['identity']]
      if p.get('ca_certificate'):
         command += ['802-1x.ca-cert', p['ca_certificate']]
      subprocess.run(['nmcli', 'connection', 'delete', p['ssid']], capture_output=True)
      result = subprocess.run(command, capture_output=True, text=True)
   else:
      ssid = escape(p['ssid'])
      profile = '''<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1"><name>{0}</name><SSIDConfig><SSID><name>{0}</name></SSID></SSIDConfig><connectionType>ESS</connectionType><connectionMode>auto</connectionMode><MSM><security><authEncryption><authentication>WPA2</authentication><encryption>AES</encryption><useOneX>true</useOneX></authEncryption><OneX xmlns="http://www.microsoft.com/networking/OneX/v1"><authMode>user</authMode><EAPConfig><EapHostConfig xmlns="http://www.microsoft.com/provisioning/EapHostConfig"><EapMethod><Type xmlns="http://www.microsoft.com/provisioning/EapCommon">25</Type><AuthorId xmlns="http://www.microsoft.com/provisioning/EapCommon">0</AuthorId></EapMethod></EapHostConfig></EAPConfig></OneX></security></MSM></WLANProfile>'''.format(ssid)
      with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8') as output:
         output.write(profile)
         filename = output.name
      result = subprocess.run(['netsh', 'wlan', 'add', 'profile', 'filename=' + filename, 'user=all'], capture_output=True, text=True)
      os.unlink(filename)
   if result.returncode:
      raise RuntimeError(result.stderr.strip() or result.stdout.strip())
   return {'ssid': p['ssid'], 'configured': True}
