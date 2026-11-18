"""Private replay input validation and solution summaries; no network traffic."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from test_rtk import frame

spec=importlib.util.spec_from_file_location('replay_tool',Path(__file__).resolve().parents[1]/'tools/replay-rtk.py')
replay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class ReplayTests(unittest.TestCase):
    def test_sources_elapsed_time_limit_and_partial_tail(self):
        a,b=frame(1077,b'abc'),frame(1074,b'def')
        record=lambda at,source,payload:struct.pack('!dBI',at,source,len(payload))+payload
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'capture.bin'
            p.write_bytes(record(10,0,a)+record(10.3,1,b)+record(10.4,2,b'CSV')+record(12,0,a)+b'partial')
            rows=list(replay.records(p,1))
            self.assertEqual([(round(t,2),s,f) for t,s,f in rows],[(0,0,a),(.3,1,b)])
            self.assertEqual(len(list(replay.records(p,10))),3)

    def test_invalid_record_cannot_silently_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'capture.bin'
            for data in (struct.pack('!dBI',0,7,0),struct.pack('!dBI',0,0,20000),struct.pack('!dBI',0,0,8)+b'not-rtcm'):
                p.write_bytes(data)
                with self.assertRaises(ValueError):list(replay.records(p,10))

    def test_summary_rejects_bad_quality_and_resets_fix_run(self):
        engine=replay.Replay('/unused',{})
        base=dict(status=1,sat=8,sigma_horizontal=.1,sigma_up=.2,age=1,ratio=4)
        engine.results=[dict(base,epoch=100),dict(base,epoch=101),dict(base,epoch=102,ratio=1),dict(base,epoch=103),dict(base,epoch=105)]
        s=engine.summary()
        self.assertEqual(s['quality'],{1:5})
        self.assertEqual(s['qualified'],{1:4})
        self.assertEqual(s['longest_fix_epochs'],2)
