#!/usr/bin/env python3
"""Run on pfSense: verify native tab links, selected tab and JSON response."""
import json
from html.parser import HTMLParser
from pathlib import Path
import subprocess


class Navigation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_navigation = False
        self.active = False
        self.anchor = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'ul' and 'nav-pills' in attrs.get('class', '').split():
            self.in_navigation = True
        if not self.in_navigation:
            return
        if tag == 'li':
            self.active = 'active' in attrs.get('class', '').split()
        elif tag == 'a':
            self.anchor = [attrs.get('href'), '', self.active]

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            self.links.append(self.anchor)
            self.anchor = None
        elif tag == 'ul':
            self.in_navigation = False


helper = Path(__file__).with_name('render-web-test.php')
for view in ('system', 'traccar', 'aprs', 'rtk', 'status'):
    html = subprocess.check_output(['php', str(helper), view], text=True)
    # pfSense renders historical PHP notices from previous package installs in
    # its notification panel. They do not indicate a failure of this request.
    assert '</html>' in html, view
    parser = Navigation()
    parser.feed(html)
    links = {label.strip(): (href, active) for href, label, active in parser.links}
    assert links.get('系统配置') == ('services_traccar.php', view == 'system'), links
    assert links.get('Traccar 配置') == ('services_traccar.php?tab=traccar', view == 'traccar'), links
    assert links.get('APRS 配置') == ('services_traccar.php?tab=aprs', view == 'aprs'), links
    assert links.get('RTK 与 MSM') == ('services_traccar.php?tab=rtk', view == 'rtk'), links
    assert links.get('状态与日志') == ('services_traccar.php?tab=status', view == 'status'), links
    assert 'href="https://github.com/ytlzq0228/pfsense_traccar"' in html
    if view != 'status':
        assert 'action="services_traccar.php?tab=' + view + '"' in html, view
        expected = {'system': 'queue_dir', 'traccar': 'device_id', 'aprs': 'aprs_callsign'}
        for section, field in expected.items():
            assert ('name="' + field + '"' in html) == (section == view), (view, field)
        if view == 'aprs':
            assert 'id="aprs-symbol-picker"' in html and 'id="aprs-symbol-grid"' in html
            assert '/traccar-symbols/picker.js?v=0.2.1' in html
            assert '/traccar-symbols/picker.css?v=0.2.1' in html
            import re
            password = re.search(r'<input[^>]+name="aprs_passcode"[^>]*>', html).group()
            assert not re.search(r'value="[^"]+"', password), 'Password must not be rendered'
        if view == 'rtk':
            import re
            password = re.search(r'<input[^>]+name="ntrip_password"[^>]*>',html).group()
            assert not re.search(r'value="[^"]+"',password), 'NTRIP password must not be rendered'
            for field in ('msm_enabled','rtk_enabled','ntrip_mountpoint','ntrip_username','rtk_max_age'):
                assert 'name="'+field+'"' in html
    if view == 'status':
        assert 'id="tracker-status"' in html and 'id="tracker-sats"' in html
        assert 'id="satellite-bars"' in html and 'id="satellite-sky"' in html
        assert '/js/traccar-satellites.js?v=0.1.2' in html
    print('PASS tab destinations, active state and rendered content:', view)
status = json.loads(subprocess.check_output(['php', str(helper), 'status-json'], text=True))
assert isinstance(status['running'], bool)
assert 'gps' in status and 'queue' in status
print('PASS status endpoint returns clean JSON')
