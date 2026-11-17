"""APRS-IS transport and timestamped positions; TCP writes are not server ACKs."""
from datetime import datetime, timezone
import math
import re
import select
import socket
import threading
import time
from urllib.parse import urlsplit

from . import __version__


def coordinate(value, latitude):
    limit = 90 if latitude else 180
    if not math.isfinite(value) or abs(value) > limit:
        raise ValueError('Invalid APRS coordinate')
    # Round before splitting so 59.995 minutes correctly carries to next degree.
    hundredths = int(math.floor(abs(value) * 6000 + 0.5))
    degrees, minutes = divmod(hundredths, 6000)
    hemisphere = ('N' if value >= 0 else 'S') if latitude else ('E' if value >= 0 else 'W')
    return f'{degrees:0{2 if latitude else 3}d}{minutes // 100:02d}.{minutes % 100:02d}{hemisphere}'


def make_packet(fix, config):
    utc = datetime.fromtimestamp(fix['epoch'], timezone.utc)
    info = '/' + utc.strftime('%d%H%Mz')
    info += coordinate(fix['lat'], True) + config['aprs_symbol_table']
    info += coordinate(fix['lon'], False) + config['aprs_symbol']
    course, speed = fix.get('bearing'), fix.get('speed')  # NMEA speed is already knots.
    if course is not None and speed is not None:
        info += f'{int(math.floor(course + 0.5)) % 360 or 360:03d}/{min(999, max(0, int(math.floor(speed + 0.5)))):03d}'
    altitude = fix.get('altitude')
    if altitude is not None:
        feet = round(altitude / 0.3048)
        if 0 <= feet <= 999999:
            info += f'/A={feet:06d}'
    # Preserve full UTC including month/year/seconds for history and duplicate detection.
    info += ' UTC=' + utc.strftime('%Y-%m-%dT%H:%M:%SZ')
    if config['aprs_comment']:
        info += ' ' + config['aprs_comment']
    packet = config['aprs_callsign'] + '>APRS,TCPIP*:' + info
    if len(info.encode('utf-8')) > 256 or len(packet.encode('utf-8')) + 2 > 512 or '\r' in packet or '\n' in packet:
        raise ValueError('APRS packet exceeds protocol limits')
    return packet


def make_item(fix, config):
    return dict(id=config['aprs_callsign'], timestamp=fix['epoch'], packet=make_packet(fix, config))


class AuthenticationError(OSError):
    pass


class Client:
    def __init__(self, timeout, passcode, stop):
        self.timeout, self.passcode, self.stop = timeout, passcode, stop
        self.socket = None
        self.identity = None
        self.buffer = b''
        self.last_rx = 0

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket, self.identity, self.buffer = None, None, b''

    def line(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if self.stop.is_set() or remaining <= 0:
                raise TimeoutError('APRS login timeout')
            self.socket.settimeout(min(0.25, remaining))
            try:
                data = self.socket.recv(1024)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError('APRS server disconnected')
            self.buffer += data
            if len(self.buffer) > 4096:
                raise OSError('APRS response too long')
        line, self.buffer = self.buffer.split(b'\n', 1)
        self.last_rx = time.monotonic()
        return line.rstrip(b'\r').decode('ascii', 'replace')

    def connect(self, url, call):
        self.close()
        endpoint = urlsplit(url)
        if endpoint.scheme != 'aprs' or not endpoint.hostname or not endpoint.port:
            raise ValueError('Invalid APRS endpoint')
        self.socket = socket.create_connection((endpoint.hostname, endpoint.port), self.timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        deadline = time.monotonic() + self.timeout
        if not self.line(deadline).startswith('#'):
            raise OSError('Invalid APRS greeting')
        if self.stop.is_set():
            raise InterruptedError('Stopping')
        self.socket.settimeout(self.timeout)
        login = f'user {call} pass {self.passcode} vers pfSenseTracker {__version__}\r\n'
        self.socket.sendall(login.encode('ascii'))
        while True:
            line = self.line(deadline)
            match = re.match(r'^#\s*logresp\s+(\S+)\s+(verified|unverified)(?:,|\s|$)', line, re.I)
            if match:
                if match[1].upper() != call.upper() or match[2].lower() != 'verified':
                    raise AuthenticationError('APRS login not verified')
                self.identity = (url, call)
                return

    def poll(self):
        if self.socket is None:
            return
        # Drain the filtered feed without logging third-party packets or blocking acquisition.
        if select.select([self.socket], [], [], 0)[0]:
            self.socket.settimeout(self.timeout)
            data = self.socket.recv(4096)
            if not data:
                raise ConnectionError('APRS server disconnected')
            self.last_rx = time.monotonic()
        if time.monotonic() - self.last_rx > 180:
            raise TimeoutError('APRS heartbeat timeout')

    def send(self, item):
        payload, url = item['payload'], item['url']
        call = payload['id']
        if self.identity != (url, call):
            self.connect(url, call)
        self.poll()
        if self.stop.is_set():
            raise InterruptedError('Stopping')
        packet = payload['packet']
        if not packet.startswith(call + '>APRS,TCPIP*:') or '\r' in packet or '\n' in packet:
            raise ValueError('Invalid queued APRS packet')
        data = (packet + '\r\n').encode('utf-8')
        if len(data) > 512:
            raise ValueError('APRS packet too long')
        self.socket.settimeout(self.timeout)
        self.socket.sendall(data)


class Sender:
    def __init__(self, config, outbox, stop, log):
        self.c, self.outbox, self.stop, self.log = config, outbox, stop, log
        self.lock = threading.Lock()
        self.state = dict(enabled=config['aprs_enabled'], state='waiting' if config['aprs_enabled'] else 'disabled',
                          verified=False, last_error='', next_retry_in=0, last_packet=None)
        self.client = Client(config['aprs_timeout'], config['aprs_passcode'], stop)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def run(self):
        if not self.c['aprs_enabled']:
            return
        failures, next_send = 0, 0
        try:
            while not self.stop.is_set():
                item = None
                try:
                    self.client.poll()
                    if time.monotonic() < next_send:
                        self.stop.wait(0.2)
                        continue
                    item = self.outbox.next(time.monotonic())
                    if item is None:
                        self.stop.wait(0.2)
                        continue
                    seq, body, attempts = item
                    self.client.send(body)
                    # APRS-IS has no per-position acknowledgement. This records only a completed TCP write.
                    self.outbox.acknowledge(seq)
                    failures = 0
                    self.update(state='online', verified=True, last_error='', next_retry_in=0,
                                last_packet=body['payload']['packet'])
                    if self.c['log_positions']:
                        self.log.info('APRS TCP write: %s', body['payload']['packet'])
                    next_send = time.monotonic() + self.c['aprs_upload_interval']
                except Exception as error:
                    self.client.close()
                    if self.stop.is_set():
                        break
                    failures += 1
                    delay = 60 if isinstance(error, AuthenticationError) else min(60, 2 ** min(failures, 6))
                    if item:
                        try:
                            self.outbox.fail(item[0], time.monotonic(), delay)
                        except Exception:
                            self.log.error('Cannot persist APRS failure; queued row retained')
                    # No login line, passcode, or untrusted server text in status/logs.
                    reason = 'APRS login not verified' if isinstance(error, AuthenticationError) else type(error).__name__ + ': APRS connection/storage failure'
                    self.update(state='retrying', verified=False, last_error=reason, next_retry_in=delay)
                    self.log.warning('%s; retained; retry in %ss', reason, delay)
                    self.stop.wait(delay)
        finally:
            self.client.close()
