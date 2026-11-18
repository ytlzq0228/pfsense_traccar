#!/usr/bin/env python3
"""Read-only native Dashboard integration check on pfSense.

Pass --enabled to require the widget to be visible in the current admin layout.
"""
import json
from pathlib import Path
import subprocess
import sys

helper=Path(__file__).with_name('render-web-test.php')
html=subprocess.check_output(['php',str(helper),'dashboard'],text=True)
assert '</html>' in html and 'GPS / RTK' in html
if '--enabled' in sys.argv:
    assert 'id="widget-traccar-0"' in html
    assert 'widgets/javascript/traccar.js?v=' in html
    assert '/widgets/css/traccar.css?v=' in html
    for field in ('state','solver','sigma','sats','age','position','ntrip','upload','updated'):
        assert 'id="traccar-mini-'+field+'"' in html
    assert '/services_traccar.php?tab=status' in html
    assert '/services_traccar.php?tab=rtk' in html
status=json.loads(subprocess.check_output(['php',str(helper),'status-json'],text=True))
assert isinstance(status['channels']['traccar'],bool)
assert isinstance(status['channels']['aprs'],bool)
print('PASS native Dashboard discovery, widget markup, assets, links and status channel flags')
