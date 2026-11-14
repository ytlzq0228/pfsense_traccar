"""Acquisition and HTTP workers are independent; GNSS UTC timestamps survive reboot."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import select
import shutil
import signal
import ssl
import sys
import termios
import threading
import time
import tty
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .config import load, DEFAULTS
from .nmea import NMEA, Sampler
from .queue import Outbox
from .usb import CH340


class Serial:
    def __init__(self, path, baud):
        self.fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(self.fd)
            a = termios.tcgetattr(self.fd)
            a[4] = a[5] = getattr(termios, 'B%d' % baud)
            a[2] |= termios.CLOCAL | termios.CREAD
            a[2] &= ~getattr(termios, 'CRTSCTS', 0)
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
        except BaseException:
            self.close()
            raise

    def read(self):
        if select.select([self.fd], [], [], 0.5)[0]:
            data = os.read(self.fd, 4096)
            if not data:
                raise OSError('Serial device disconnected')
            return data
        return b''

    def close(self):
        os.close(self.fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post(item, timeout):
    # Never follow redirects to an unrelated endpoint or treat login pages as delivery.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    payload = dict(item['payload'])
    # OsmAnd rejects floating-point epoch strings; ISO UTC also preserves fractions.
    payload['timestamp'] = datetime.fromtimestamp(float(payload['timestamp']), timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    data = urllib.parse.urlencode(payload).encode('ascii')
    request = urllib.request.Request(item['url'], data=data, headers={
        'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'pfSense-Traccar/' + __version__})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        return code


def make_payload(fix, config, moving):
    p = dict(id=config['device_id'], timestamp=fix['epoch'], lat=round(fix['lat'],7),
             lon=round(fix['lon'],7), valid='true', sat=fix['sat'], hdop=fix['hdop'],
             motion='true' if moving else 'false', fixQuality=fix['quality'])
    for key in ('speed','bearing','altitude'):
        if fix[key] is not None:
            p[key] = round(fix[key], 3)
    return p


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data, f, allow_nan=False, separators=(',', ':'))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def logger(name, path, config, raw=False):
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.propagate = False
    handler = RotatingFileHandler(path, maxBytes=config['log_mb']*1024*1024, backupCount=config['log_backups'])
    handler.setFormatter(logging.Formatter('%(message)s' if raw else '%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    return log


class Service:
    def __init__(self, config, status='/var/run/traccar-status.json'):
        self.c = config
        self.directory = Path(config['queue_dir'])
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.lock = (self.directory/'service.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.log = logger('traccar', self.directory/'service.log', config)
        self.positions = logger('traccar.positions', self.directory/'positions.jsonl', config, True) if config['log_positions'] else None
        self.raw = logger('traccar.nmea', self.directory/'nmea.log', config, True) if config['log_nmea'] else None
        self.outbox = Outbox(self.directory/'queue.sqlite', config['max_points'], config['queue_mb'])
        self.stop = threading.Event()
        self.status = Path(status)
        self.state_lock = threading.Lock()
        self.network = dict(state='waiting', last_http=None, last_error='', next_retry_in=0)
        self.storage_error = ''
        self.started = time.time()
        self.sent_worker = threading.Thread(target=self.upload, daemon=True)

    def upload(self):
        failures = 0
        while not self.stop.is_set():
            try:
                item = self.outbox.next(time.monotonic())
                if item is None:
                    self.stop.wait(0.2)
                    continue
                seq, body, attempts = item
                code = post(body, self.c['http_timeout'])
                if 200 <= code < 300:
                    self.outbox.acknowledge(seq)
                    failures = 0
                    with self.state_lock:
                        self.network.update(state='online', last_http=code, last_error='', next_retry_in=0)
                else:
                    raise DeliveryError(code)
            except Exception as e:
                # Preserve all non-2xx, including bad configuration; no silent HTTP 4xx drops.
                code = e.code if isinstance(e, DeliveryError) else None
                failures += 1
                delay = min(60, 2**min(failures,6)) + random.uniform(0,1)
                if code and 400 <= code < 500 and code not in (408,429):
                    delay = 60
                error = 'HTTP %d' % code if code else type(e).__name__ + ': connection/storage failure'
                if 'item' in locals() and item:
                    try:
                        self.outbox.fail(item[0], time.monotonic(), delay)
                    except Exception:
                        self.log.exception('Cannot persist delivery failure')
                with self.state_lock:
                    self.network.update(state='retrying', last_http=code, last_error=error, next_retry_in=round(delay,1))
                self.log.warning('Upload failed (%s); retained; retry in %.1fs', error, delay)
                self.stop.wait(delay)
                continue
            self.stop.wait(1/self.c['upload_rate'])

    def run(self):
        parser, sampler, source = NMEA(), Sampler(self.c), None
        reconnect_at, last_state, next_status, last_valid_data = 0, None, 0, 0
        backoff = 1
        self.log.info('Starting version=%s source=%s device=%s', __version__, self.c['source'], self.c['device_id'])
        self.sent_worker.start()
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                try:
                    if source is None and now >= reconnect_at:
                        source = CH340(self.c['baud'], self.c['usb_selector']) if self.c['source'] == 'ch340' else Serial(self.c['serial_path'], self.c['baud'])
                        # Invalidate ALL epochs from the old connection.
                        parser = NMEA()
                        sampler.was_valid = False
                        last_valid_data = now
                        self.log.info('GPS opened: %s', getattr(source, 'name', self.c['serial_path']))
                    if source is not None:
                        lines = parser.feed(source.read(), time.monotonic())
                        if lines:
                            last_valid_data, backoff = time.monotonic(), 1
                            if self.raw:
                                for line in lines:
                                    self.raw.info(line)
                        if time.monotonic()-last_valid_data > max(15, self.c['fix_timeout']*2):
                            raise OSError('No valid NMEA; reconnecting (check baud rate)')
                    else:
                        self.stop.wait(0.25)
                except (OSError, ValueError) as e:
                    if source:
                        source.close()
                        source = None
                    parser.valid, parser.reason = False, str(e)
                    reconnect_at = time.monotonic()+backoff
                    self.log.warning('GPS unavailable: %s; retry in %ss', e, backoff)
                    backoff = min(30, backoff*2)
                now = time.monotonic()
                gps = parser.snapshot(now, self.c['fix_timeout'], self.c['max_hdop'], self.c['min_sat'])
                state = (gps['valid'], gps['reason'])
                if state != last_state:
                    self.log.info('GPS state: %s', gps['reason'])
                    last_state = state
                fix = sampler.choose(gps, now)
                if fix:
                    try:
                        if shutil.disk_usage(self.directory).free < self.c['reserve_mb']*1024*1024:
                            raise OSError('Disk reserve reached; acquisition paused')
                        payload = make_payload(fix, self.c, sampler.moving)
                        if self.outbox.enqueue(payload, self.c['server_url']) and self.positions:
                            self.positions.info(json.dumps(payload, separators=(',', ':')))
                        self.storage_error = ''
                    except Exception as e:
                        error = str(e)
                        if error != self.storage_error:
                            self.log.error('Cannot queue position: %s', error)
                        self.storage_error = error
                        sampler.last_sample = None
                if now >= next_status:
                    with self.state_lock:
                        network = dict(self.network)
                    state = dict(version=__version__, pid=os.getpid(), updated=time.time(), started=self.started,
                                 gps=gps, moving=sampler.moving, queue=self.outbox.stats(), network=network,
                                 storage_error=self.storage_error, free_bytes=shutil.disk_usage(self.directory).free,
                                 source=self.c['source'], device=getattr(source, 'name', self.c['serial_path']) if source else None)
                    atomic_json(self.status, state)
                    next_status = now+1
        finally:
            self.stop.set()
            if source:
                source.close()
            self.sent_worker.join(timeout=self.c['http_timeout']+2)
            if not self.sent_worker.is_alive():
                self.outbox.close()
            self.log.info('Stopped')
            self.lock.close()


class DeliveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('HTTP %d' % code)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='/usr/local/etc/traccar.json')
    p.add_argument('--validate', action='store_true')
    p.add_argument('--defaults', action='store_true')
    a = p.parse_args()
    if a.defaults:
        print(json.dumps(DEFAULTS))
        return
    try:
        config = load(a.config)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    if a.validate:
        return
    if not config['enabled']:
        return
    service = Service(config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop.set())
    service.run()


if __name__ == '__main__':
    main()
