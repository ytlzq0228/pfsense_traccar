"""All synthetic APRS positions are sent exclusively to loopback servers."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import logging

from pftracker.aprs import Client, Sender, AuthenticationError, coordinate, make_packet, make_item
from pftracker.config import DEFAULTS, validate
from pftracker.queue import Outbox
from test_integration import until


CONFIG = {**DEFAULTS, 'aprs_enabled': True, 'aprs_callsign': 'N0CALL-PF',
          'aprs_passcode': '12345', 'aprs_host': '127.0.0.1', 'aprs_comment': '测试 car',
          'aprs_timeout': 1, 'aprs_upload_interval': 0.05}
FIX = dict(epoch=datetime(2026, 11, 14, 12, 34, 56, tzinfo=timezone.utc).timestamp(),
           lat=40.5, lon=-116.25, speed=10, bearing=0, altitude=30.48)


@contextmanager
def receiver(verified=True, respond=True):
    records = {'logins': [], 'packets': []}
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                self.wfile.write(b'# local-test 1\r\n'); self.wfile.flush()
                login = self.rfile.readline().decode('ascii').strip()
                if not login:
                    return
                records['logins'].append(login)
                call = login.split()[1]
                if not respond:
                    time.sleep(0.5)
                    return
                response = f'# logresp {call} {"verified" if verified else "unverified"}, server LOCAL\r\n'.encode()
                self.wfile.write(response[:12]); self.wfile.flush()
                self.wfile.write(response[12:]); self.wfile.flush()
                while True:
                    line = self.rfile.readline()
                    if not line:
                        break
                    records['packets'].append(line)
            except (OSError, ValueError):
                pass
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True
    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'aprs://127.0.0.1:{server.server_address[1]}', records
    finally:
        server.shutdown(); server.server_close(); thread.join()


class PacketTests(unittest.TestCase):
    def test_packet_units_unicode_original_utc(self):
        packet = make_packet(FIX, CONFIG)
        self.assertEqual(packet, 'N0CALL-PF>APRS,TCPIP*:/141234z4030.00N/11615.00W>360/010/A=000100 UTC=2026-11-14T12:34:56Z 测试 car')
        self.assertNotIn('qAC', packet)
        self.assertNotIn('12345', packet)

    def test_rounding_hemispheres_and_bounds(self):
        self.assertEqual(coordinate(12.9999999, True), '1300.00N')
        self.assertEqual(coordinate(-0.5, True), '0030.00S')
        self.assertEqual(coordinate(180, False), '18000.00E')
        for value in (float('nan'), float('inf'), 90.1):
            with self.assertRaises(ValueError): coordinate(value, True)

    def test_unknown_motion_and_negative_altitude(self):
        packet = make_packet({**FIX, 'bearing': None, 'speed': None, 'altitude': -10}, CONFIG)
        self.assertIn('> UTC=', packet)
        self.assertNotIn('/A=', packet)

    def test_injection_rejected(self):
        with self.assertRaises(ValueError):
            make_packet(FIX, {**CONFIG, 'aprs_comment': 'a\r\nINJECT'})


class ConfigurationTests(unittest.TestCase):
    def test_upgrade_defaults_and_aprs_only(self):
        old = validate({'enabled': True, 'server_url': 'http://localhost', 'device_id': 'original'})
        self.assertTrue(old['traccar_enabled']); self.assertFalse(old['aprs_enabled'])
        self.assertTrue(validate({**CONFIG, 'aprs_upload_interval': 5, 'enabled': True, 'traccar_enabled': False})['aprs_enabled'])

    def test_invalid_aprs_fields(self):
        for item in ({'aprs_callsign': 'TOOLONG-PF'}, {'aprs_callsign': 'CALL-0'}, {'aprs_callsign': 'A\nB'},
                     {'aprs_passcode': '-1'}, {'aprs_passcode': '123\n'}, {'aprs_host': 'host:14580'},
                     {'aprs_comment': '中'*54}, {'aprs_symbol': '>>'}, {'aprs_port': 1.5},
                     {'aprs_moving_interval': 1}, {'aprs_enabled': True}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate(item)
        for symbol in ('/', '\\', 'A', '0'):
            self.assertEqual(validate({'aprs_symbol_table': symbol})['aprs_symbol_table'], symbol)


class TransportTests(unittest.TestCase):
    def test_verified_login_persistent_connection_crlf_and_utf8(self):
        with receiver() as (url, records):
            stop = threading.Event(); client = Client(1, '12345', stop)
            try:
                for offset in (0, 30):
                    client.send({'url': url, 'payload': make_item({**FIX, 'epoch': FIX['epoch']+offset}, CONFIG)})
                until(lambda: len(records['packets']) == 2)
                self.assertEqual(len(records['logins']), 1)
                self.assertIn('user N0CALL-PF pass 12345 vers pfSenseTracker ', records['logins'][0])
                self.assertTrue(records['packets'][0].endswith(b'\r\n'))
                self.assertIn('测试'.encode(), records['packets'][0])
            finally:
                client.close()

    def test_unverified_never_sends_position(self):
        with receiver(verified=False) as (url, records):
            client = Client(1, 'wrong', threading.Event())
            try:
                with self.assertRaises(AuthenticationError):
                    client.send({'url': url, 'payload': make_item(FIX, CONFIG)})
                self.assertEqual(records['packets'], [])
            finally:
                client.close()

    def test_login_timeout_and_stop(self):
        with receiver(respond=False) as (url, records):
            stop = threading.Event(); client = Client(0.1, '12345', stop)
            try:
                with self.assertRaises(TimeoutError): client.connect(url, 'N0CALL-PF')
                self.assertEqual(records['packets'], [])
            finally:
                client.close()

    def test_persistent_queue_recovery_latest_first_original_packets(self):
        with receiver() as (url, records), tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'aprs.sqlite'
            q = Outbox(path)
            for offset in (0, 30): q.enqueue(make_item({**FIX, 'epoch': FIX['epoch']+offset}, CONFIG), url)
            q.close(); q = Outbox(path)
            q.enqueue(make_item({**FIX, 'epoch': FIX['epoch']+60}, CONFIG), url)
            stop = threading.Event(); sender = Sender(CONFIG, q, stop, logging.getLogger('aprs-test'))
            original = sender.client.send
            attempts = []
            def temporarily_offline(body):
                attempts.append(body)
                if len(attempts) == 1:
                    raise ConnectionError('local simulated outage')
                return original(body)
            with patch.object(sender.client, 'send', side_effect=temporarily_offline):
                thread = threading.Thread(target=sender.run, daemon=True); thread.start()
                try:
                    until(lambda: q.stats()['failures'] == 1)
                    self.assertEqual(q.stats()['count'], 3)
                    # A fresh live sample has priority over retained failed/history records.
                    q.enqueue(make_item({**FIX, 'epoch': FIX['epoch']+90}, CONFIG), url)
                    until(lambda: q.stats()['count'] == 0)
                    until(lambda: len(records['packets']) == 4)
                    self.assertIn(b'12:36:26Z', records['packets'][0])
                    self.assertEqual(q.stats()['sent'], 4)
                    self.assertTrue(sender.snapshot()['verified'])
                    self.assertNotIn('12345', str(sender.snapshot()))
                    for offset in (0, 30, 60, 90):
                        self.assertIn((make_packet({**FIX, 'epoch': FIX['epoch']+offset}, CONFIG)+'\r\n').encode(), records['packets'])
                finally:
                    stop.set(); thread.join(3); q.close()
                    self.assertFalse(thread.is_alive())

    def test_disabled_channel_keeps_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            q = Outbox(Path(directory)/'q.sqlite')
            q.enqueue(make_item(FIX, CONFIG), 'aprs://127.0.0.1:1')
            sender = Sender({**CONFIG, 'aprs_enabled': False}, q, threading.Event(), logging.getLogger('aprs-test'))
            sender.run()
            self.assertEqual(q.stats()['count'], 1)
            self.assertEqual(sender.snapshot()['state'], 'disabled')
            q.close()

class AcquisitionTests(unittest.TestCase):
    def test_aprs_only_fix_loss_recovery_and_usb_reconnect(self):
        from pftracker.service import Service
        from test_tracker import sentence
        controls = {'valid': True, 'disconnect': False, 'opens': 0}
        class USB:
            name = 'synthetic-local-only'
            def __init__(self, *args): controls['opens'] += 1
            def close(self): pass
            def read(self):
                time.sleep(0.06)
                if controls['disconnect']:
                    controls['disconnect'] = False
                    raise OSError('synthetic disconnect')
                utc = datetime.now(timezone.utc)
                tod = utc.strftime('%H%M%S.%f')[:10]
                valid = controls['valid']
                return sentence('GNGGA,%s,4000.000,N,11600.000,E,%s,12,0.8,40,M,0,M,,' % (tod, 1 if valid else 0)) + sentence('GNRMC,%s,%s,4000.000,N,11600.000,E,0,0,%s,,,A' % (tod, 'A' if valid else 'V', utc.strftime('%d%m%y')))
        with receiver() as (url, records), tempfile.TemporaryDirectory() as directory:
            config = {**CONFIG, 'enabled': True, 'traccar_enabled': False, 'queue_dir': directory,
                      'aprs_port': int(url.rsplit(':', 1)[1]), 'aprs_stopped_interval': 0.2,
                      'reserve_mb': 16, 'log_positions': False}
            service = Service(config, str(Path(directory)/'status.json'))
            with patch('pftracker.service.CH340', USB), patch('pftracker.service.post') as http:
                thread = threading.Thread(target=service.run, daemon=True); thread.start()
                try:
                    until(lambda: len(records['packets']) >= 2)
                    controls['valid'] = False
                    time.sleep(0.5)
                    frozen = len(records['packets'])
                    time.sleep(0.6)
                    self.assertEqual(len(records['packets']), frozen)
                    controls['valid'] = True
                    until(lambda: len(records['packets']) > frozen)
                    controls['disconnect'] = True
                    until(lambda: controls['opens'] >= 2)
                    before = len(records['packets'])
                    until(lambda: len(records['packets']) > before)
                    self.assertEqual(service.outbox.stats()['count'], 0)
                    http.assert_not_called()
                finally:
                    service.stop.set(); thread.join(4)
                    self.assertFalse(thread.is_alive())
