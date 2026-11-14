import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pftracker.config import DEFAULTS
from pftracker.nmea import NMEA
from pftracker.service import make_payload, Service
from pftracker.temperature import read_temperature
from test_tracker import epoch


class TemperatureTests(unittest.TestCase):
    def read(self, output):
        with patch('pftracker.temperature.subprocess.run', return_value=SimpleNamespace(stdout=output)):
            return read_temperature()

    def test_prefer_hottest_cpu_over_acpi(self):
        value, source = self.read('dev.cpu.0.temperature: 48.0C\ndev.cpu.1.temperature: 50.5C\nhw.acpi.thermal.tz0.temperature: 70.0C\n')
        self.assertEqual((value, source), (50.5, 'CPU'))

    def test_acpi_fallback_excludes_trip_threshold(self):
        self.assertEqual(self.read('hw.acpi.thermal.tz0._CRT: 125.1C\nhw.acpi.thermal.tz0.temperature: 47.1C\n'), (47.1, 'ACPI'))

    def test_missing_and_invalid_sensors(self):
        self.assertEqual(self.read('dev.cpu.0.temperature: -273.1C\nhw.acpi.thermal.tz0.temperature: nanC\n'), (None, None))
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('sysctl', .5)):
            with patch('pftracker.temperature.subprocess.run', side_effect=error):
                self.assertEqual(read_temperature(), (None, None))

    def test_temperature_payload_and_omission(self):
        parser=NMEA(); epoch(parser)
        self.assertEqual(make_payload(parser.fix, DEFAULTS, False, 47.14)['deviceTemp'],47.1)
        for value in (None, float('nan'), float('inf'), -273.1):
            self.assertNotIn('deviceTemp',make_payload(parser.fix,DEFAULTS,False,value))

    def test_stale_temperature_not_reused(self):
        import threading
        service=Service.__new__(Service)
        service.state_lock=threading.Lock()
        service.temperature=(47.1, 'ACPI', 10)
        with patch('pftracker.service.time.monotonic', return_value=20):
            self.assertEqual(service.temperature_snapshot(),(47.1,'ACPI'))
        with patch('pftracker.service.time.monotonic', return_value=41):
            self.assertEqual(service.temperature_snapshot(),(None,None))
