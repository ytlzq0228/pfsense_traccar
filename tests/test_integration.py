"""Failure tests use a loopback receiver and synthetic NMEA, never a real server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs

from pftracker.config import DEFAULTS
from pftracker.queue import Outbox
from pftracker.service import Service
from test_tracker import sentence


def until(predicate, timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(0.1)
    raise AssertionError('Timed out waiting for condition')


class FailureIntegrationTests(unittest.TestCase):
    def test_inflight_crash_leaves_point(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'crash.sqlite'
            script='''
import os,sys
from pftracker.queue import Outbox
q=Outbox(sys.argv[1]); q.enqueue({'id':'test','timestamp':123,'lat':1,'lon':2},'http://127.0.0.1')
assert q.next(1) is not None
os._exit(9)
'''
            result=subprocess.run([sys.executable,'-c',script,str(path)])
            self.assertEqual(result.returncode,9)
            q=Outbox(path)
            try:
                self.assertEqual(q.stats()['count'],1)
                self.assertEqual(q.next(1)[1]['payload']['timestamp'],123)
            finally: q.close()

    def test_capture_outage_recovery_usb_and_upload(self):
        controls={'network':False,'gps':True,'disconnect':False,'opens':0,'accepted':[]}
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body=parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                if controls['network']:
                    controls['accepted'].append(body)
                    self.send_response(200)
                else: self.send_response(503)
                self.end_headers()
            def log_message(self,*_): pass
        server=HTTPServer(('127.0.0.1',0),Receiver)
        http_thread=threading.Thread(target=server.serve_forever,daemon=True); http_thread.start()
        class FakeUSB:
            name='test-USB'
            def __init__(self,*_): controls['opens']+=1
            def close(self): pass
            def read(self):
                time.sleep(0.1)
                if controls['disconnect']:
                    controls['disconnect']=False
                    raise OSError('simulated USB unplug')
                utc=datetime.now(timezone.utc)
                tod=utc.strftime('%H%M%S')
                valid=controls['gps']
                return sentence('GNGGA,%s,4000.000,N,11600.000,E,%s,12,0.8,40,M,0,M,,'%(tod,1 if valid else 0))+sentence('GNRMC,%s,%s,4000.000,N,11600.000,E,0,0,%s,,,A'%(tod,'A' if valid else 'V',utc.strftime('%d%m%y')))
        with tempfile.TemporaryDirectory() as directory, patch('pftracker.service.CH340',FakeUSB):
            config={**DEFAULTS,'enabled':True,'server_url':'http://127.0.0.1:%s'%server.server_port,
                    'device_id':'SYNTHETIC-LOCAL-ONLY','queue_dir':directory,'stopped_interval':1,
                    'fix_timeout':2,'http_timeout':1,'reserve_mb':16,'log_positions':False}
            service=Service(config,str(Path(directory)/'status.json'))
            thread=threading.Thread(target=service.run,daemon=True); thread.start()
            try:
                until(lambda:service.outbox.stats()['count']>=3)
                self.assertGreater(service.outbox.stats()['failures'],0)
                controls['gps']=False
                time.sleep(0.4)
                frozen=service.outbox.stats()['count']
                time.sleep(1.3)
                self.assertEqual(service.outbox.stats()['count'],frozen)
                controls['network']=True
                until(lambda:service.outbox.stats()['count']==0,timeout=15)
                self.assertEqual(len(controls['accepted']),frozen)
                self.assertTrue(all(p['timestamp'][0].endswith('Z') for p in controls['accepted']))
                controls['gps']=True
                until(lambda:len(controls['accepted'])>frozen)
                controls['disconnect']=True
                until(lambda:controls['opens']>=2)
                before=len(controls['accepted'])
                until(lambda:len(controls['accepted'])>before)
            finally:
                service.stop.set(); thread.join(5)
                server.shutdown(); server.server_close(); http_thread.join()
                self.assertFalse(thread.is_alive())
