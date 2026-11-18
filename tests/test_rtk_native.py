"""Optional native solver smoke test, with no GNSS or external network access."""
import os
import threading
import time
import unittest

from pftracker.config import DEFAULTS
from pftracker.rtk import RTK


@unittest.skipUnless(os.environ.get('PFTRACKER_RTKLIB'),'set PFTRACKER_RTKLIB on FreeBSD to test native helper')
class NativeSolverTests(unittest.TestCase):
    def test_headless_inputs_and_clean_shutdown(self):
        stop=threading.Event()
        rtk=RTK(DEFAULTS,stop,os.environ['PFTRACKER_RTKLIB'])
        rtk.start()
        try:
            end=time.monotonic()+12
            while time.monotonic()<end and rtk.snapshot()['connected_inputs']!=2:
                time.sleep(.1)
            state=rtk.snapshot()
            self.assertEqual(state['connected_inputs'],2,state)
            self.assertIsNone(state['solution'])
            self.assertEqual(state['ntrip']['state'],'waiting_gga')
            self.assertIsNone(rtk.process.poll())
            child=rtk.process
            time.sleep(6)
            self.assertIs(rtk.process,child,'quiet local streams must not restart the solver')
            self.assertIsNone(child.poll())
            child.terminate()
            end=time.monotonic()+8
            while time.monotonic()<end and (rtk.process is child or rtk.snapshot()['connected_inputs']!=2):
                time.sleep(.1)
            self.assertIsNot(rtk.process,child)
            self.assertEqual(rtk.snapshot()['connected_inputs'],2)
            self.assertIsNone(rtk.snapshot()['solution'])
        finally:
            stop.set();rtk.close()
        self.assertFalse(rtk.worker.is_alive())
        self.assertFalse(rtk.network_worker.is_alive())
