"""Acquisition and HTTP workers are independent; GNSS UTC timestamps survive reboot."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import queue
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
from .temperature import read_temperature
from .aprs import Sender as APRSSender, make_item as make_aprs_item
from .rtcm import MixedStream, sentence
from .rtk import RTK


class Serial:
    def __init__(self, path, baud):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
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

    def write(self, data):
        if os.write(self.fd,data) != len(data):
            raise OSError('Incomplete GNSS command write')


class BufferedSource:
    """Continuously drain UART bursts while the consumer parses or syncs SQLite."""
    def __init__(self, source):
        # CH340 has a small UART FIFO; a CPU-bound parser must not hold the
        # interpreter for the default 5 ms while the USB reader waits for it.
        sys.setswitchinterval(min(sys.getswitchinterval(),0.0005))
        self.source=source
        self.name=getattr(source,'name','serial')
        self.pending=queue.Queue(1024)
        self.stop=threading.Event()
        self.error=None
        self.worker=threading.Thread(target=self.run,daemon=True)
        self.worker.start()

    def run(self):
        try:
            while not self.stop.is_set():
                data=self.source.read()
                if data:
                    try:self.pending.put_nowait((time.monotonic(),data))
                    except queue.Full:raise OSError('GNSS receive buffer overflow') from None
        except (OSError,ValueError) as error:
            self.error=error

    def read(self):
        if self.error:raise self.error
        try:at,data=self.pending.get(timeout=.1)
        except queue.Empty:return b''
        if time.monotonic()-at>1:
            raise OSError('GNSS receive buffer is stale')
        return data

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)
        if self.worker.is_alive():raise OSError('GNSS reader did not stop')
        self.source.close()


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


def make_payload(fix, config, moving, device_temp=None):
    p = dict(id=config['device_id'], timestamp=fix['epoch'], lat=round(fix['lat'],7),
             lon=round(fix['lon'],7), valid='true', sat=fix['sat'],
             motion='true' if moving else 'false', fixQuality=fix['quality'])
    if fix.get('hdop') is not None:
        p['hdop']=fix['hdop']
    if fix.get('accuracy') is not None:
        p['accuracy']=round(fix['accuracy'],3)
    if 'rtk_age' in fix:
        p.update(positionSource='rtk_fix' if fix['quality']==4 else 'rtk_float',
                 rtkAge=fix['rtk_age'], rtkRatio=fix['rtk_ratio'])
    else:
        p['positionSource']='gga'
    if device_temp is not None and math.isfinite(device_temp) and -40 <= device_temp <= 150:
        p['deviceTemp'] = round(device_temp, 1)
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
        self.aprs_outbox = Outbox(self.directory/'aprs.sqlite', config['max_points'], config['queue_mb'])
        self.stop = threading.Event()
        self.status = Path(status)
        self.state_lock = threading.Lock()
        self.network = dict(state='waiting' if config['traccar_enabled'] else 'disabled', last_http=None, last_error='', next_retry_in=0)
        self.temperature = (None, None, None)
        self.temperature_worker = threading.Thread(target=self.update_temperature, daemon=True)
        self.storage_error = ''
        self.started = time.time()
        self.sent_worker = threading.Thread(target=self.upload, daemon=True)
        self.aprs = APRSSender(config, self.aprs_outbox, self.stop, self.log)
        self.aprs_worker = threading.Thread(target=self.aprs.run, daemon=True)
        self.storage_errors = {}
        self.rtk = RTK(config,self.stop) if config['rtk_enabled'] else None

    def update_temperature(self):
        # Hardware reads never block the USB acquisition or HTTP sender.
        while not self.stop.is_set():
            value, source = read_temperature()
            with self.state_lock:
                self.temperature = (value, source, time.monotonic())
            self.stop.wait(10)

    def temperature_snapshot(self):
        with self.state_lock:
            value, source, sampled = self.temperature
        if sampled is None or time.monotonic()-sampled > 30:
            return None, None
        return value, source

    def upload(self):
        if not self.c['traccar_enabled']:
            return
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

    def capture(self, fix, sampler, aprs=False):
        channel = 'APRS' if aprs else 'Traccar'
        try:
            if shutil.disk_usage(self.directory).free < self.c['reserve_mb']*1024*1024:
                raise OSError('Disk reserve reached; acquisition paused')
            if aprs:
                payload = make_aprs_item(fix, self.c)
                endpoint = 'aprs://%s:%s' % (self.c['aprs_host'], self.c['aprs_port'])
                queued = self.aprs_outbox.enqueue(payload, endpoint)
            else:
                payload = make_payload(fix, self.c, sampler.moving, self.temperature_snapshot()[0])
                queued = self.outbox.enqueue(payload, self.c['server_url'])
            if queued and self.positions:
                self.positions.info(json.dumps({'channel': channel, **payload}, ensure_ascii=False, separators=(',', ':')))
            self.storage_errors.pop(channel, None)
        except Exception as error:
            message = channel + ': ' + str(error)
            if self.storage_errors.get(channel) != message:
                self.log.error('Cannot queue position: %s', message)
            self.storage_errors[channel] = message
            sampler.last_sample = None
        self.storage_error = '; '.join(self.storage_errors.values())

    def run(self):
        parser, sampler, source = NMEA(), Sampler(self.c), None
        framing, latest_gga = MixedStream(), None
        aprs_sampler = Sampler({**self.c, 'moving_interval': self.c['aprs_moving_interval'],
                                'stopped_interval': self.c['aprs_stopped_interval']})
        reconnect_at, last_state, next_status, last_valid_data = 0, None, 0, 0
        backoff = 1
        self.log.info('Starting version=%s source=%s device=%s', __version__, self.c['source'], self.c['device_id'])
        self.temperature_worker.start()
        self.sent_worker.start()
        self.aprs_worker.start()
        if self.rtk:
            self.rtk.start()
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                try:
                    if source is None and now >= reconnect_at:
                        source = CH340(self.c['baud'], self.c['usb_selector']) if self.c['source'] == 'ch340' else Serial(self.c['serial_path'], self.c['baud'])
                        # Invalidate ALL epochs from the old connection.
                        parser = NMEA()
                        framing, latest_gga = MixedStream(), None
                        sampler.was_valid = aprs_sampler.was_valid = False
                        last_valid_data = now
                        self.log.info('GPS opened: %s', getattr(source, 'name', self.c['serial_path']))
                        if self.c['msm_enabled']:
                            source.write(sentence('PAIR432,'+('1' if self.c['msm_mode']=='msm7' else '0')))
                            source.write(sentence('PAIR436,1'))
                        source=BufferedSource(source)
                    if source is not None:
                        lines=[]
                        for kind,message in framing.feed(source.read()):
                            if kind=='rtcm':
                                if self.rtk: self.rtk.feed_rover(message)
                            else:
                                parsed=parser.feed(message,time.monotonic())
                                lines.extend(parsed)
                                for line in parsed:
                                    if line[3:6]=='GGA': latest_gga=line
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
                    latest_gga = None
                    if self.rtk: self.rtk.receiver_lost()
                    reconnect_at = time.monotonic()+backoff
                    self.log.warning('GPS unavailable: %s; retry in %ss', e, backoff)
                    backoff = min(30, backoff*2)
                now = time.monotonic()
                gps = parser.snapshot(now, self.c['fix_timeout'], self.c['max_hdop'], self.c['min_sat'])
                if self.rtk:
                    self.rtk.ntrip.set_gga(latest_gga if gps['valid'] else None)
                    gps=self.rtk.select(gps)
                else:
                    gps.update(source='gga',rtk_used=False)
                state = (gps['valid'], gps['reason'])
                if state != last_state:
                    self.log.info('GPS state: %s', gps['reason'])
                    last_state = state
                fix = sampler.choose(gps, now)
                if fix and self.c['traccar_enabled']:
                    self.capture(fix, sampler)
                aprs_fix = aprs_sampler.choose(gps, now)
                if aprs_fix and self.c['aprs_enabled']:
                    self.capture(aprs_fix, aprs_sampler, aprs=True)
                if now >= next_status:
                    with self.state_lock:
                        network = dict(self.network)
                    device_temp, temp_source = self.temperature_snapshot()
                    state = dict(device_temp=device_temp, device_temp_source=temp_source, version=__version__, pid=os.getpid(), updated=time.time(), started=self.started,
                                 gps=gps, moving=sampler.moving, queue=self.outbox.stats(), network=network,
                                 aprs={**self.aprs.snapshot(), 'queue': self.aprs_outbox.stats()},
                                 rtcm=framing.snapshot(now), rtk=self.rtk.snapshot() if self.rtk else {'state':'disabled'},
                                 storage_error=self.storage_error, free_bytes=shutil.disk_usage(self.directory).free,
                                 source=self.c['source'], device=getattr(source, 'name', self.c['serial_path']) if source else None)
                    atomic_json(self.status, state)
                    next_status = now+1
        finally:
            self.stop.set()
            if source:
                source.close()
            if self.rtk:
                self.rtk.close()
            self.temperature_worker.join(timeout=1)
            self.sent_worker.join(timeout=self.c['http_timeout']+2)
            self.aprs_worker.join(timeout=self.c['aprs_timeout']+2)
            if not self.aprs_worker.is_alive():
                self.aprs_outbox.close()
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
