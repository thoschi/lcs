import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core


class PlatformTests(unittest.TestCase):
   def setUp(self):
      self.directory = tempfile.TemporaryDirectory()
      self.addCleanup(self.directory.cleanup)
      database = patch.object(core, 'DB_PATH', Path(self.directory.name) / 'lcs.sqlite3')
      database.start()
      self.addCleanup(database.stop)
      core.init_db()
      self.server = importlib.import_module('server')
      admins = patch.object(self.server, 'ADMIN_USERS', {'test'})
      admins.start()
      self.addCleanup(admins.stop)
      self.client = self.server.app.test_client()
      self.enrollment = core.add_enrollment_token('test', password='test-password',
                                                 template=False, token_type='shared')

   def enroll(self, platform):
      response = self.client.post('/api/v1/enroll', json={
         'hostname': 'same-client', 'platform': platform, 'enrollment_token': self.enrollment})
      self.assertEqual(response.status_code, 200)
      return response.get_json()

   def heartbeat(self, device, exam=False):
      response = self.client.post('/api/v1/heartbeat', headers={
         'X-Device-ID': device['device_id'], 'Authorization': 'Bearer ' + device['device_token']},
         json={'hostname': 'same-client', 'hardware': {'exam_mode': exam}})
      self.assertEqual(response.status_code, 200)
      return self.server.dashboard_data()[0][0]

   def test_saved_ubuntu_token_replaces_linbo_and_preserves_history(self):
      ubuntu = self.enroll('linux')
      linbo = self.enroll('linbo')
      self.assertEqual(ubuntu['device_id'], linbo['device_id'])
      self.assertEqual(self.heartbeat(linbo)['platform_filter'], 'linbo')
      device = self.heartbeat(ubuntu)
      self.assertEqual(device['platform'], 'linux')
      self.assertEqual(device['platform_filter'], 'linux')
      self.assertEqual([p['value'] for p in device['platforms'] if p['current']], ['linux'])
      self.assertIn('linbo', json.loads(device['platform_history_json']))
      self.assertEqual(self.heartbeat(linbo)['platform_filter'], 'linbo')

   def test_exam_mode_changes_current_filter_without_erasing_ubuntu(self):
      ubuntu = self.enroll('linux')
      self.heartbeat(ubuntu)
      device = self.heartbeat(ubuntu, exam=True)
      self.assertEqual(device['platform_filter'], 'exam')
      self.assertEqual([p['label'] for p in device['platforms'] if p['current']], ['EXM'])
      device = self.heartbeat(ubuntu)
      self.assertEqual(device['platform_filter'], 'linux')
      self.assertEqual({p['value'] for p in device['platforms']}, {'linux', 'exam'})

   def test_windows_token_restores_windows_after_linbo(self):
      windows = self.enroll('windows')
      self.enroll('linbo')
      self.assertEqual(self.heartbeat(windows)['platform_filter'], 'windows')

   def test_exam_credential_returns_to_ubuntu_when_squid_stops(self):
      ubuntu = self.enroll('linux')
      with core.db() as conn:
         conn.execute("UPDATE device_credentials SET platform='exam' WHERE device_id=?",
                      (ubuntu['device_id'],))
         conn.execute("UPDATE devices SET settings_json=? WHERE id=?",
                      (json.dumps({'LCS_EXAM_MODE': 'true'}), ubuntu['device_id']))
      self.enroll('linbo')
      self.assertEqual(self.heartbeat(ubuntu, exam=True)['platform_filter'], 'exam')
      self.assertEqual(self.heartbeat(ubuntu)['platform_filter'], 'linux')

   def test_invalid_token_cannot_change_platform(self):
      device = self.enroll('linbo')
      response = self.client.post('/api/v1/heartbeat', headers={
         'X-Device-ID': device['device_id'], 'Authorization': 'Bearer invalid'},
         json={'platform': 'linux', 'hardware': {'exam_mode': True}})
      self.assertEqual(response.status_code, 401)
      self.assertEqual(self.server.dashboard_data()[0][0]['platform_filter'], 'linbo')

   def test_filter_buttons_are_available_without_online_devices(self):
      with self.client.session_transaction() as session:
         session['admin'] = {'username': 'test'}
         session['csrf'] = 'test'
      response = self.client.get('/admin/clients')
      self.assertEqual(response.status_code, 200)
      for value, label in [('linbo', 'LBO'), ('linux', 'UBN'), ('windows', 'WIN'), ('exam', 'EXM')]:
         self.assertIn(f'data-value="{value}" aria-pressed="false">{label}</button>', response.get_data(as_text=True))


if __name__ == '__main__':
   unittest.main()
