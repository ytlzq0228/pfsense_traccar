"""NTRIP client. Credentials never appear in URLs, status, argv, or logs."""
import base64
import re
import socket
import threading
import time

from .rtcm import MixedStream, sentence


class Chunked:
    def __init__(self):
        self.buffer = bytearray()
        self.remaining = None

    def feed(self, data):
        self.buffer.extend(data)
        out = bytearray()
        while True:
            if self.remaining is None:
                i = self.buffer.find(b'\r\n')
                if i < 0:
                    if len(self.buffer) > 128:
                        raise ValueError('Invalid chunk header')
                    break
                try:
                    self.remaining = int(self.buffer[:i].split(b';')[0], 16)
                except ValueError:
                    raise ValueError('Invalid NTRIP chunk size') from None
                del self.buffer[:i+2]
                if not 0 < self.remaining <= 1048576:
                    raise ValueError('NTRIP stream ended or chunk too large')
            if len(self.buffer) < self.remaining+2:
                break
            if self.buffer[self.remaining:self.remaining+2] != b'\r\n':
                raise ValueError('Invalid chunk terminator')
            out.extend(self.buffer[:self.remaining])
            del self.buffer[:self.remaining+2]
            self.remaining = None
        return bytes(out)


def response(sock):
    """Return initial body and transfer encoding, handling NTRIP v1 ICY too."""
    data = bytearray()
    while b'\r\n' not in data:
        part = sock.recv(4096)
        if not part:
            raise OSError('Caster closed before response')
        data.extend(part)
        if len(data) > 16384:
            raise ValueError('NTRIP response header too large')
    first, rest = bytes(data).split(b'\r\n', 1)
    match = re.fullmatch(rb'(?:HTTP/1\.[01]|ICY) (\d{3})(?: .*)?', first)
    if not match or match[1] != b'200':
        # Never include an untrusted response or reflected authorization in errors.
        raise OSError('NTRIP HTTP '+(match[1].decode() if match else 'invalid response'))
    if first.startswith(b'ICY '):
        # v1 may have no empty line or headers. The RTCM framer ignores optional
        # ASCII response headers without consuming binary payload as line text.
        return rest, False
    data = bytearray(rest)
    while b'\r\n\r\n' not in data:
        if data.startswith(b'\r\n'):
            return bytes(data[2:]), False
        part = sock.recv(4096)
        if not part:
            raise OSError('Caster closed in headers')
        data.extend(part)
        if len(data) > 16384:
            raise ValueError('NTRIP response header too large')
    headers, body = bytes(data).split(b'\r\n\r\n', 1)
    headers = {k.strip().lower():v.strip().lower() for row in headers.split(b'\r\n')
               if b':' in row for k,v in [row.split(b':',1)]}
    if b'sourcetable' in headers.get(b'content-type',b''):
        raise OSError('Caster returned sourcetable instead of corrections')
    if headers.get(b'content-encoding',b'identity') != b'identity':
        raise OSError('Unsupported NTRIP content encoding')
    return body, headers.get(b'transfer-encoding')==b'chunked'


class Ntrip:
    def __init__(self, config, stop, on_frame, on_session=lambda: None):
        self.c, self.stop, self.on_frame, self.on_session = config, stop, on_frame, on_session
        self.lock = threading.Lock()
        self.gga = None
        self.sock = None
        self.last_frame = None
        self.bytes = self.frames = self.sessions = 0
        self.state, self.error, self.mountpoint = 'waiting_gga', '', config['ntrip_mountpoint']

    def set_gga(self, line):
        with self.lock:
            if line is None:
                self.gga = None
            else:
                body = line[1:].split('*', 1)[0]
                if body[2:5] == 'GGA':
                    self.gga = (sentence('GP'+body[2:]), time.monotonic())

    def get_gga(self):
        with self.lock:
            value = self.gga
        return value[0] if value and time.monotonic()-value[1] <= self.c['fix_timeout'] else None

    def snapshot(self):
        with self.lock:
            return dict(state=self.state, last_error=self.error, mountpoint=self.mountpoint,
                        bytes=self.bytes, valid_frames=self.frames, sessions=self.sessions,
                        correction_age=None if self.last_frame is None else max(0, time.monotonic()-self.last_frame))

    def change(self, **values):
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)

    def close(self):
        with self.lock:
            sock = self.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def stream(self, mountpoint):
        self.change(state='connecting', mountpoint=mountpoint, last_frame=None)
        sock = socket.create_connection((self.c['ntrip_host'], self.c['ntrip_port']), timeout=5)
        self.change(sock=sock)
        try:
            gga = self.get_gga()
            if not gga:
                return
            auth = base64.b64encode((self.c['ntrip_username']+':'+self.c['ntrip_password']).encode()).decode('ascii')
            request = (f'GET /{mountpoint} HTTP/1.1\r\nHost: {self.c["ntrip_host"]}:{self.c["ntrip_port"]}\r\n'
                       'User-Agent: NTRIP pfSense-Traccar\r\nNtrip-Version: Ntrip/2.0\r\n'
                       f'Authorization: Basic {auth}\r\nNtrip-GGA: {gga.decode().strip()}\r\n'
                       'Accept: */*\r\nConnection: close\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            initial, chunked = response(sock)
            self.on_session()
            with self.lock:
                self.sessions += 1
            self.change(state='waiting_corrections')
            sock.sendall(gga)
            sock.settimeout(1)
            decoder, framing = (Chunked() if chunked else None), MixedStream()
            began, last_gga = time.monotonic(), time.monotonic()
            pending = initial
            while not self.stop.is_set():
                now = time.monotonic()
                if pending:
                    data = decoder.feed(pending) if decoder else pending
                    with self.lock:
                        self.bytes += len(data)
                    for kind, frame in framing.feed(data, now):
                        if kind != 'rtcm':
                            continue
                        self.on_frame(frame)
                        with self.lock:
                            self.frames += 1
                            self.last_frame, self.state, self.error = now, 'streaming', ''
                if now-last_gga >= 5:
                    gga = self.get_gga()
                    if not gga:
                        raise OSError('Fresh GGA unavailable')
                    sock.sendall(gga)
                    last_gga = now
                with self.lock:
                    last = self.last_frame
                if now-(last or began) > 15:
                    raise OSError('No valid RTCM correction frames for 15 seconds')
                try:
                    pending = sock.recv(16384)
                    if not pending:
                        raise OSError('Caster disconnected')
                except socket.timeout:
                    pending = b''
        finally:
            self.change(sock=None, last_frame=None)
            sock.close()

    def run(self):
        preferred = self.c['ntrip_mountpoint']
        mounts = list(dict.fromkeys([preferred, 'RTCM32_GGB' if preferred == 'AUTO' else 'AUTO']))
        failures = 0
        while not self.stop.is_set():
            if not self.get_gga():
                self.change(state='waiting_gga')
                self.stop.wait(1)
                continue
            for mount in mounts:
                if self.stop.is_set():
                    return
                try:
                    self.stream(mount)
                    if self.stop.is_set():
                        return
                except (OSError, ValueError) as error:
                    self.change(state='retry', error=str(error), last_frame=None)
                if not self.get_gga():
                    break
            failures += 1
            self.stop.wait(min(60, 2**min(failures, 6)))
