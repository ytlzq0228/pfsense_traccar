import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from pftracker.config import DEFAULTS, validate
from pftracker.nmea import NMEA, Sampler
from pftracker.queue import Outbox
from pftracker.service import make_payload, post
from pftracker.usb import baud_register


def sentence(body):
    checksum = 0
    for b in body.encode():
        checksum ^= b
    return ('$%s*%02X\r\n' % (body, checksum)).encode()


def epoch(parser, at=1, utc='120001.000', date='141126', speed='0.00', status='A', quality=1, hdop='0.8'):
    parser.feed(sentence('GNGGA,%s,4000.000,N,11600.000,E,%s,12,%s,40.0,M,0,M,,' % (utc,quality,hdop)), at)
    parser.feed(sentence('GNRMC,%s,%s,4000.000,N,11600.000,E,%s,90.0,%s,,,A' % (utc,status,speed,date)), at)


class ParserTests(unittest.TestCase):
    def test_live_and_stale(self):
        p = NMEA(); epoch(p)
        self.assertTrue(p.snapshot(1)['valid'])
        self.assertFalse(p.snapshot(7)['valid'])
        self.assertEqual(p.fix['lat'], 40)
        self.assertEqual(p.fix['lon'], 116)

    def test_bad_checksum_fragment_and_buffer_limit(self):
        p = NMEA()
        line = sentence('GPGSV,1,1,01,01,50,120,30,1')
        p.feed(line[:12], 1); self.assertEqual(p.valid_lines,0)
        p.feed(line[12:],1); self.assertEqual(p.valid_lines,1)
        p.feed(b'$GNRMC,garbage*00\n',1); self.assertEqual(p.bad_lines,1)
        p.feed(b'a'*10000,1); self.assertLess(len(p.buffer),1025)

    def test_loss_and_recovery(self):
        p = NMEA(); epoch(p)
        epoch(p,2,'120002',status='V',quality=0)
        self.assertFalse(p.snapshot(2)['valid'])
        epoch(p,3,'120003')
        self.assertTrue(p.snapshot(3)['valid'])

    def test_mismatched_epochs(self):
        p = NMEA()
        p.feed(sentence('GNGGA,120001,4000.000,N,11600.000,E,1,12,0.8,40,M,0,M,,'),1)
        p.feed(sentence('GNRMC,120002,A,4000.000,N,11600.000,E,0,0,141126,,,A'),1)
        self.assertFalse(p.snapshot(1)['valid'])

    def test_frozen_timestamp_expires(self):
        p = NMEA(); epoch(p)
        epoch(p,10)
        self.assertFalse(p.snapshot(10)['valid'])

    def test_weak_fix_and_missing_hdop(self):
        for hdop in ('20','', '0'):
            p = NMEA(); epoch(p,hdop=hdop)
            self.assertFalse(p.snapshot(1)['valid'])

    def test_midnight_and_backwards(self):
        p = NMEA(); epoch(p,1,'235959')
        previous = p.fix['epoch']
        epoch(p,2,'000000',date='151126')
        self.assertEqual(p.fix['epoch'],previous+1)
        epoch(p,3,'235958',date='141126')
        self.assertEqual(p.fix['epoch'],previous+1)

    def test_satellite_cycles_and_expiry(self):
        p = NMEA()
        p.feed(sentence('GNGSA,A,3,21,,,,,,,,,,,,1.0,0.8,0.6,3'),1)
        p.feed(sentence('GAGSV,2,1,05,21,60,120,40,22,30,100,30,23,20,90,,24,10,70,10,7'),1)
        self.assertEqual(len(p.snapshot(1)['satellites']),0)
        p.feed(sentence('GAGSV,2,2,05,25,15,70,20,7'),1)
        sats = p.snapshot(1)['satellites']
        self.assertEqual(len(sats),5)
        self.assertTrue(sats[0]['used'])
        self.assertEqual(len(p.snapshot(17)['satellites']),0)

    def test_nan_rejected(self):
        p = NMEA(); epoch(p,speed='nan')
        self.assertFalse(p.snapshot(1)['valid'])

    def test_fix_before_rmc_same_epoch(self):
        p = NMEA(); epoch(p)
        p.feed(sentence('GNRMC,120002,A,4000.000,N,11600.000,E,0,0,141126,,,A'),2)
        p.feed(sentence('GNGGA,120002,4000.000,N,11600.000,E,1,12,0.8,40,M,0,M,,'),2)
        self.assertEqual(p.fix['t'],43202)


class SamplingTests(unittest.TestCase):
    def test_intervals_hysteresis_recovery(self):
        p, s = NMEA(), Sampler(DEFAULTS)
        epoch(p,1,'120001'); self.assertIsNotNone(s.choose(p.snapshot(1),1))
        epoch(p,2,'120002'); self.assertIsNone(s.choose(p.snapshot(2),2))
        epoch(p,3,'120003',speed='5'); self.assertIsNone(s.choose(p.snapshot(3),3))
        epoch(p,6,'120006',speed='5'); self.assertIsNotNone(s.choose(p.snapshot(6),6)); self.assertTrue(s.moving)
        epoch(p,7,'120007',speed='5'); self.assertIsNotNone(s.choose(p.snapshot(7),7))
        epoch(p,8,'120008',speed='0'); s.choose(p.snapshot(8),8)
        epoch(p,23,'120023',speed='0'); s.choose(p.snapshot(23),23); self.assertFalse(s.moving)
        epoch(p,24,'120024',status='V'); self.assertIsNone(s.choose(p.snapshot(24),24))
        epoch(p,25,'120025'); self.assertIsNotNone(s.choose(p.snapshot(25),25))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'queue.sqlite'
        self.q = Outbox(self.path,3,1)
    def tearDown(self):
        self.q.close(); self.tmp.cleanup()
    def add(self, t, url='http://localhost'):
        return self.q.enqueue(dict(id='test', timestamp=t, lat=1,lon=2),url)
    def test_no_delete_until_ack_and_restart(self):
        self.add(1); item = self.q.next(1)
        self.assertEqual(self.q.stats()['count'],1)
        self.q.close(); self.q=Outbox(self.path)
        self.assertEqual(self.q.next(2)[0],item[0])
        self.q.acknowledge(item[0]); self.assertEqual(self.q.stats()['count'],0)
    def test_realtime_priority_and_history(self):
        self.add(1); self.add(2); self.add(3)
        latest=self.q.next(1); self.assertEqual(latest[1]['payload']['timestamp'],3)
        self.q.acknowledge(latest[0])
        old=self.q.next(1); self.assertEqual(old[1]['payload']['timestamp'],1)
        self.add(4); self.assertEqual(self.q.next(1)[1]['payload']['timestamp'],4)
    def test_overflow(self):
        for t in range(1,6): self.add(t)
        self.assertEqual(self.q.stats()['count'],3)
        self.assertEqual(self.q.stats()['dropped'],2)
        self.assertEqual(self.q.stats()['oldest'],3)
    def test_failed_item_retained_and_backoff(self):
        self.add(1); self.add(2)
        seq=self.q.next(1)[0]; self.q.fail(seq,1,60)
        self.assertEqual(self.q.stats()['count'],2)
        self.assertNotEqual(self.q.next(2)[0],seq)
    def test_dedup_and_destination_pinned(self):
        self.assertTrue(self.add(1))
        self.assertFalse(self.add(1))
        self.assertTrue(self.add(1,'http://second'))
        self.assertEqual(self.q.next(1)[1]['url'],'http://second')
    def test_size_eviction(self):
        self.q.max_bytes=400
        for t in range(1,10): self.add(t)
        self.assertLessEqual(self.q.stats()['payload_bytes'],400)
        self.assertGreater(self.q.stats()['dropped'],0)


class HTTPTests(unittest.TestCase):
    def test_http_failures_redirect_and_payload(self):
        bodies=[]
        class Handler(BaseHTTPRequestHandler):
            code=200
            def do_POST(self):
                bodies.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(self.code)
                if self.code == 302: self.send_header('Location','/login')
                self.end_headers()
            def log_message(self,*args): pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            for code in (200,400,429,500,302):
                Handler.code=code
                self.assertEqual(post(dict(url='http://127.0.0.1:%d/'%server.server_port,payload={'id':'test','timestamp':1794634600.125}),1),code)
            self.assertIn(b'timestamp=2026-11-14T05%3A36%3A40.125Z',bodies[0])
        finally:
            server.shutdown(); server.server_close(); thread.join()


class ConfigTests(unittest.TestCase):
    def test_defaults(self): self.assertEqual(validate({})['source'],'ch340')
    def test_bad_values(self):
        for data in ({'queue_dir':'/var/tmp/x'}, {'queue_dir':'/conf/../etc'}, {'upload_rate':0}, {'moving_interval':float('nan')}, {'enabled':True}, {'speed_hysteresis':10}):
            with self.assertRaises(ValueError): validate(data)
    def test_baud(self): self.assertEqual(baud_register(115200),0xcc03)

if __name__ == '__main__': unittest.main()
