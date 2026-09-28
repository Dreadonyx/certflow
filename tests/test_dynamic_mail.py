"""Regression checks; uses an isolated database and never sends real email."""
import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = os.path.join(_temp.name, 'test.db')
import app as module
from PIL import Image


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        module.limiter.enabled = False
        self.client = module.app.test_client()
        with self.client.session_transaction() as session:
            session['authenticated'] = True
            session['_csrf_token'] = 'test-token'
        self.headers = {'X-CSRFToken': 'test-token'}
        with closing(sqlite3.connect(module.DB_PATH)) as conn, conn:
            for table in ('send_jobs', 'mailer_state', 'mailer_queue'):
                conn.execute(f'DELETE FROM {table}')

    def post(self, url, payload):
        return self.client.post(url, json=payload, headers=self.headers)

    def payload(self):
        return {'jobId': 'test-job-1234567890', 'smtpUser': 'sender@example.com',
                'smtpPassword': 'fake', 'smtpProvider': 'gmail', 'attachCert': False,
                'emailSubject': '{string4}', 'participants': [
                    {'name': 'Alice', 'values': ['Alice', 'IT', 'Gold', '2026'], 'email': 'a@example.com'},
                    {'name': 'Bob', 'values': ['Bob', 'IT', 'Gold', '2026'], 'email': 'b@example.com'}]}

    def test_dynamic_csv_and_render(self):
        response = self.client.post('/parse-csv', data={
            'headerMode': 'yes', 'csvFile': (io.BytesIO(b'Email,Full name,Dept,Award,Year,Event\na@example.com,"Alice, A",IT,Gold,2026,Meet\n'), 'people.csv')}, headers=self.headers)
        data = response.get_json()
        self.assertEqual(data['columns'], ['Full name', 'Dept', 'Award', 'Year', 'Event'])
        participant = data['participants'][0]
        self.assertEqual(participant['values'][0], 'Alice, A')
        self.assertEqual(module.personalize('{string5} {string4}', participant), 'Meet 2026')
        buf = io.BytesIO(); Image.new('RGB', (300, 200), 'white').save(buf, 'PNG')
        template = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
        settings = {'fields': [{'column': 4, 'X': 10, 'Y': 10, 'FontSize': 20, 'Color': '#000000', 'Font': 'arial.ttf'}]}
        preview = self.post('/generate', dict(participant, template=template, **settings))
        self.assertTrue(preview.get_json()['success'])
        image = Image.open(io.BytesIO(base64.b64decode(preview.get_json()['image'].split(',')[1])))
        self.assertNotEqual(image.getextrema(), ((255, 255),) * 3)
        batch = self.post('/generate-batch', {'participants': [participant], 'template': template, 'settings': settings})
        self.assertEqual(batch.status_code, 200)

    def test_disconnect_retries_only_unsent_and_keeps_placeholders(self):
        calls = []
        class SMTP:
            def sendmail(self, sender, recipient, message):
                calls.append(recipient)
                if len(calls) == 2:
                    raise module.smtplib.SMTPServerDisconnected('offline')
                assert 'Subject: 2026' in message
                self.message = message
            def quit(self): pass
        with patch.object(module, '_make_smtp_connection', return_value=SMTP()):
            first = self.post('/send-certificates', self.payload()).get_data(as_text=True)
            self.assertIn('"type": "retry"', first)
            second = self.post('/send-certificates', self.payload()).get_data(as_text=True)
            self.assertIn('"sent": 2', second)
            third = self.post('/send-certificates', self.payload()).get_data(as_text=True)
            self.assertIn('"sent": 2', third)
        self.assertEqual(calls, ['a@example.com', 'b@example.com', 'b@example.com'])

    def test_pause_resume_and_ownership(self):
        calls = []
        class SMTP:
            def sendmail(inner, sender, recipient, message):
                calls.append(recipient)
                if len(calls) == 1:
                    with closing(sqlite3.connect(module.DB_PATH)) as conn, conn:
                        conn.execute('UPDATE send_jobs SET paused=1')
            def quit(inner): pass
        with patch.object(module, '_make_smtp_connection', return_value=SMTP()):
            first = self.post('/send-certificates', self.payload()).get_data(as_text=True)
            self.assertIn('"type": "paused"', first)
            control = '/send-jobs/test-job-1234567890/control'
            self.assertEqual(self.client.post(control, json={'action': 'resume'}).status_code, 403)
            self.assertEqual(self.post(control, {'action': 'resume'}).status_code, 200)
            self.assertIn('"sent": 2', self.post('/send-certificates', self.payload()).get_data(as_text=True))
        self.assertEqual(calls, ['a@example.com', 'b@example.com'])
        with self.client.session_transaction() as session:
            session['_csrf_token'] = 'different'
        self.assertEqual(self.client.post(control, json={'action': 'pause'}, headers={'X-CSRFToken': 'different'}).status_code, 404)

    def test_throttled_pause_and_connection_retry(self):
        with closing(sqlite3.connect(module.DB_PATH)) as conn, conn:
            conn.execute("INSERT INTO mailer_queue (email,name) VALUES ('a@example.com','Alice')")
        self.assertEqual(self.post('/mailer/control', {'action': 'pause'}).status_code, 200)
        with patch.object(module, '_tm_send_one', side_effect=OSError('offline')) as send:
            self.assertEqual(self.client.get('/mailer/send-next').get_json()['status'], 'manual_pause')
            send.assert_not_called()
            self.post('/mailer/control', {'action': 'resume'})
            self.assertEqual(self.client.get('/mailer/send-next').get_json()['status'], 'retry_wait')
        self.assertEqual(self.client.get('/mailer/status').get_json()['pending'], 1)
        module._mstate_set('tm_retry_at', '0')
        with patch.object(module, '_tm_send_one') as send:
            self.assertEqual(self.client.get('/mailer/send-next').get_json()['status'], 'sent')
            send.assert_called_once()

    def test_connection_failure_and_active_lease(self):
        with patch.object(module, '_make_smtp_connection', side_effect=OSError('offline')):
            self.assertIn('"type": "retry"', self.post('/send-certificates', self.payload()).get_data(as_text=True))
        with closing(sqlite3.connect(module.DB_PATH)) as conn, conn:
            conn.execute('UPDATE send_jobs SET lease=?', (module.time.time() + 60,))
        with patch.object(module, '_make_smtp_connection') as connect:
            self.assertEqual(self.post('/send-certificates', self.payload()).status_code, 409)
            connect.assert_not_called()

    def test_legacy_headerless_csv_and_field_validation(self):
        participants, columns = module.parse_csv_rows([['Alice', 'IT', 'a@example.com']])
        self.assertEqual(columns, ['String 1', 'String 2'])
        self.assertEqual(participants[0]['email'], 'a@example.com')
        participants, columns = module.parse_csv_rows([['Alice', 'IT', 'Gold', '2026', 'Meet']], 'no')
        self.assertEqual(len(columns), 5)
        with self.assertRaises(ValueError):
            module.validate_settings({'fields': [{'column': -1}]})
        with self.assertRaises(ValueError):
            module.validate_participants([{'name': 'Alice', 'values': ['x' * 201]}])

    def test_smtp_classification(self):
        self.assertFalse(module.retryable_mail_error(module.smtplib.SMTPAuthenticationError(535, b'bad password')))
        self.assertTrue(module.retryable_mail_error(module.smtplib.SMTPDataError(451, b'try later')))
        self.assertFalse(module.retryable_mail_error(module.smtplib.SMTPRecipientsRefused({'x': (550, b'no')})))


if __name__ == '__main__':
    unittest.main()
