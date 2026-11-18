"""Position attributes describe their acquisition epoch, including GGA fallback."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler,HTTPServer

from pftracker.config import DEFAULTS
from pftracker.queue import Outbox
from pftracker.rtk import select_position,RTK
from pftracker.service import make_payload,post


class PayloadStatusTests(unittest.TestCase):
    def setUp(self):
        self.config={**DEFAULTS,'device_id':'test','rtk_enabled':True}
        self.gga=dict(valid=True,fix_age=.2,fix=dict(epoch=1795000000,lat=40.,lon=116.,quality=1,
            sat=12,hdop=.8,speed=0.,bearing=0.,altitude=50.,geoid_separation=-6.))
        self.solution=dict(epoch=1795000000,lat=40.00001,lon=116.00001,ellipsoid_height=44.,status=1,
            sat=9,sigma_horizontal=.02345,sigma_up=.04,age=1.,ratio=5.5)
        self.state=dict(state='fix',solution=self.solution,solution_age=.1,msm_age=.2,
            ntrip=dict(state='streaming',correction_age=.3,password='must-not-send',last_error='private-error'))

    def payload(self,state=None,selected=None):
        r=state or self.state
        if selected is None:
            selected=select_position(self.gga,r['solution'],r['solution_age'],r['ntrip']['correction_age'],r['msm_age'],self.config)
        return make_payload(selected['fix'],self.config,False,47.1,dict(rtk=r,gga=self.gga,
            fallback_reason=selected.get('fallback_reason'),rtcm=dict(valid_frames=100,bad_crc=2)))

    def test_fixed_native_and_custom_fields(self):
        p=self.payload()
        self.assertEqual(p['status'],'FIX');self.assertEqual(p['positionSource'],'rtk_fix')
        self.assertEqual(p['fixQuality'],4);self.assertEqual(p['sat'],9)
        self.assertEqual(p['accuracy'],.023);self.assertEqual(p['hdop'],.8)
        self.assertEqual(p['rtkUsed'],'true');self.assertEqual(p['rtkState'],'fix')
        self.assertEqual(p['rtkQuality'],1);self.assertEqual(p['rtkSigmaV'],.04)
        self.assertEqual(p['rtkRatio'],5.5);self.assertEqual(p['rtkAge'],1)
        self.assertEqual(p['rtkGgaTimeOffset'],0);self.assertEqual(p['rtkFallbackReason'],'none')
        self.assertEqual(p['rtkSolutionFresh'],'true');self.assertEqual(p['ntripState'],'streaming')
        self.assertEqual(p['rtcmCrcErrors'],2);self.assertTrue(p['rtkTimestamp'].endswith('Z'))
        self.assertNotIn('must-not-send',json.dumps(p));self.assertNotIn('private-error',json.dumps(p))

    def test_float_status(self):
        r={**self.state,'state':'float','solution':{**self.solution,'status':2,'ratio':1.2}}
        p=self.payload(r)
        self.assertEqual(p['status'],'FLOAT');self.assertEqual(p['fixQuality'],5)
        self.assertEqual(p['rtkQuality'],2);self.assertEqual(p['rtkRatio'],1.2)

    def test_gga_fallback_keeps_solver_diagnostics_without_false_accuracy(self):
        r={**self.state,'solution':{**self.solution,'sigma_horizontal':8.0}}
        p=self.payload(r)
        self.assertEqual(p['status'],'GGA');self.assertEqual(p['positionSource'],'gga')
        self.assertEqual(p['rtkUsed'],'false');self.assertEqual(p['fixQuality'],1)
        self.assertNotIn('accuracy',p)
        self.assertEqual(p['rtkSigmaH'],8.0);self.assertEqual(p['rtkState'],'fix')
        self.assertEqual(p['rtkFallbackReason'],'RTK uncertainty too large')
        stale=self.payload({**self.state,'solution_age':20.})
        self.assertEqual(stale['rtkSolutionFresh'],'false');self.assertNotIn('accuracy',stale)

    def test_disabled_and_unknown_values_are_not_fabricated(self):
        p=make_payload(self.gga['fix'],DEFAULTS,False,diagnostics=dict(rtk={'state':'disabled'},gga=self.gga))
        self.assertEqual(p['rtkEnabled'],'false');self.assertEqual(p['rtkUsed'],'false')
        for field in ('accuracy','rtkQuality','rtkSigmaH','rtkRatio','ntripAge','rtkTimestamp'):self.assertNotIn(field,p)
        r={**self.state,'solution':{**self.solution,'sigma_up':float('nan'),'ratio':float('inf')}}
        p=self.payload(r,selected={**self.gga,'fallback_reason':'bad solver'})
        self.assertNotIn('rtkSigmaV',p);self.assertNotIn('rtkRatio',p)
        json.dumps(p,allow_nan=False)

    def test_outbox_freezes_original_state_across_reopen(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'queue.sqlite';q=Outbox(path)
            p=self.payload();q.enqueue(p,'http://127.0.0.1');q.close()
            self.solution.update(status=2,ratio=1.)
            q=Outbox(path)
            try:
                old=q.next(100)[1]['payload']
                self.assertEqual(old['status'],'FIX');self.assertEqual(old['rtkRatio'],5.5)
            finally:q.close()

    def test_post_serializes_boolean_numeric_and_status_attributes(self):
        received=[]
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()))
                self.send_response(200);self.end_headers()
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Receiver)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            self.assertEqual(post(dict(url=f'http://127.0.0.1:{server.server_port}',payload=self.payload()),2),200)
            form=received[0]
            self.assertEqual(form['rtkUsed'],['true']);self.assertEqual(form['status'],['FIX'])
            self.assertEqual(form['rtkSigmaH'],['0.023']);self.assertEqual(form['ntripAge'],['0.3'])
            self.assertTrue(form['rtkTimestamp'][0].endswith('Z'))
        finally:server.shutdown();server.server_close();worker.join()

    def test_selection_uses_the_supplied_single_snapshot(self):
        engine=RTK(self.config,threading.Event())
        with patch.object(engine,'snapshot',side_effect=AssertionError('second snapshot')):
            selected=engine.select(self.gga,self.state)
        self.assertEqual(selected['source'],'rtk_fix')

    def test_native_visible_satellites_deduplicate_signals(self):
        self.gga['satellites']=[dict(system='GPS',prn=1,signal='1'),
            dict(system='GPS',prn=1,signal='7'),dict(system='Galileo',prn=1,signal='1')]
        p=self.payload()
        self.assertEqual(p['satVisible'],2)
        self.assertEqual(p['ggaValid'],'true')
