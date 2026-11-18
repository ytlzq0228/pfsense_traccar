"""Supervised RTKLIB solver fed by the sole USB reader and an NTRIP worker."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import tempfile
import threading
import time

from .ntrip import Ntrip


def parse_solution(line):
    if line.startswith('%'):
        return None
    fields = [x.strip() for x in line.split(',')]
    if len(fields) < 14:
        return None
    epoch = datetime.strptime(fields[0], '%Y/%m/%d %H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp()
    lat, lon, height = map(float, fields[1:4])
    quality, satellites = int(fields[4]), int(fields[5])
    north, east, up = map(float, fields[6:9])
    age, ratio = float(fields[12]), float(fields[13])
    values = (epoch, lat, lon, height, north, east, up, age, ratio)
    if not all(math.isfinite(v) for v in values) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Invalid solver coordinates')
    if epoch < 1577836800 or not 0 <= satellites <= 256 or min(north,east,up) < 0:
        raise ValueError('Invalid solver quality')
    return dict(epoch=epoch, lat=lat, lon=lon, ellipsoid_height=height, status=quality,
                sat=satellites, sigma_horizontal=math.hypot(north,east), sigma_up=up, age=age, ratio=ratio)


def select_position(gga, solution, solution_age, correction_age, msm_age, config):
    """Fail closed on stale, weak, non-RTK or frozen solutions; GGA is independent."""
    selected = {**gga, 'source': 'gga', 'rtk_used': False}
    reason = 'waiting for RTK solution'
    if solution:
        if solution['status'] not in (1,2):
            reason = 'solver has no RTK FIX/FLOAT'
        elif solution['status'] == 2 and not config['rtk_allow_float']:
            reason = 'RTK FLOAT disabled'
        elif any(v is None for v in (solution_age, correction_age, msm_age)):
            reason = 'RTK input disconnected'
        elif solution_age > config['fix_timeout'] or msm_age > config['fix_timeout']:
            reason = 'RTK solution or rover observations stale'
        elif correction_age > config['rtk_max_age'] or abs(solution['age']) > config['rtk_max_age']:
            reason = 'RTK corrections stale'
        elif solution['sat'] < max(4, config['min_sat']):
            reason = 'too few RTK satellites'
        elif solution['sigma_horizontal'] > config['rtk_max_sigma'] or solution['sigma_up'] > config['rtk_max_sigma']*2:
            reason = 'RTK uncertainty too large'
        elif solution['status'] == 1 and solution['ratio'] < 3:
            reason = 'RTK ambiguity ratio below 3'
        elif gga.get('valid') and abs(solution['epoch']-gga['fix']['epoch']) > 1.5:
            reason = 'RTK/GGA epochs do not match'
        else:
            raw = gga.get('fix') or {}
            separation = raw.get('geoid_separation') if gga.get('valid') else None
            fix = dict(epoch=solution['epoch'], lat=solution['lat'], lon=solution['lon'],
                       quality=4 if solution['status']==1 else 5, sat=solution['sat'],
                       hdop=raw.get('hdop') if gga.get('valid') else None,
                       speed=raw.get('speed') if gga.get('valid') else None,
                       bearing=raw.get('bearing') if gga.get('valid') else None,
                       altitude=None if separation is None else solution['ellipsoid_height']-separation,
                       accuracy=solution['sigma_horizontal'], rtk_age=solution['age'], rtk_ratio=solution['ratio'])
            return {**gga, 'valid':True, 'reason':'RTK FIX' if solution['status']==1 else 'RTK FLOAT',
                    'source':'rtk_fix' if solution['status']==1 else 'rtk_float',
                    'rtk_used':True, 'fix':fix, 'fix_age':solution_age, 'fallback_reason':''}
    selected['fallback_reason'] = reason
    return selected


class RTK:
    def __init__(self, config, stop, executable='/usr/local/libexec/pftracker-rtkrcv'):
        self.c, self.stop, self.executable = config, stop, executable
        self.lock = threading.Lock()
        self.restart = threading.Event()
        self.rover, self.base = queue.Queue(128), queue.Queue(128)
        self.solution = self.solution_at = self.last_msm = None
        self.state, self.error, self.dropped = 'starting', '', 0
        self.process = None
        self.connected_inputs = 0
        self.ntrip = Ntrip(config, stop, lambda frame: self.feed(self.base, frame), self.reset)
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.network_worker = threading.Thread(target=self.ntrip.run, daemon=True)

    def start(self):
        self.worker.start()
        self.network_worker.start()

    def reset(self):
        for q in (self.rover,self.base):
            while not q.empty():
                try: q.get_nowait()
                except queue.Empty: break
        with self.lock:
            self.solution = self.solution_at = None
        self.restart.set()

    def receiver_lost(self):
        self.ntrip.set_gga(None)
        with self.lock:
            self.last_msm = None
        self.reset()

    def feed(self, destination, frame):
        try:
            destination.put_nowait(frame)
        except queue.Full:
            with self.lock:
                self.dropped += 1
                self.solution = self.solution_at = None
            self.reset()

    def feed_rover(self, frame):
        kind = (frame[3]<<4)|(frame[4]>>4)
        if 1071 <= kind <= 1127 and kind%10 in (4,5,6,7):
            with self.lock:
                self.last_msm = time.monotonic()
        self.feed(self.rover, frame)

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            result = dict(state=self.state, last_error=self.error, solution=self.solution,
                          solution_age=None if self.solution_at is None else now-self.solution_at,
                          msm_age=None if self.last_msm is None else now-self.last_msm, dropped_frames=self.dropped,
                          connected_inputs=self.connected_inputs)
        result['ntrip'] = self.ntrip.snapshot()
        result['datum'] = 'WGS84 / reference epoch 2005.0 (caster-defined; no epoch transformation)'
        return result

    def select(self, gga):
        state = self.snapshot()
        return select_position(gga, state['solution'], state['solution_age'],
                               state['ntrip']['correction_age'], state['msm_age'], self.c)

    @staticmethod
    def listener():
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', 0))
        sock.listen(1)
        sock.setblocking(False)
        return sock

    def configuration(self, ports):
        settings = {
            'pos1-posmode':'kinematic', 'pos1-frequency':'l1', 'pos1-soltype':'forward',
            # Keep the phase filter advancing when noisy standalone code
            # positioning fails. Each RTK epoch still needs relative observations
            # and passes the unchanged external freshness/sigma/ratio gates.
            'pos1-dynamics':'on',
            'pos1-elmask':15, 'pos1-ionoopt':'brdc', 'pos1-tropopt':'saas', 'pos1-sateph':'brdc',
            'pos1-navsys':61, 'pos2-armode':'continuous', 'pos2-gloarmode':'off',
            # Conservative consumer-receiver model: 1 cm phase / 3 m code.
            # The independent solution sigma/ratio gates still apply.
            'stats-errphase':0.01, 'stats-errphaseel':0.01, 'stats-eratio1':300,
            'pos1-snrmask_r':'on', 'pos1-snrmask_L1':','.join(['30']*9),
            'pos2-arthres':3, 'pos2-arlockcnt':10, 'pos2-maxage':self.c['rtk_max_age'],
            'ant2-postype':'rtcm', 'out-solformat':'llh', 'out-timesys':'utc', 'out-timeform':'hms',
            'out-timendec':3, 'out-degform':'deg', 'out-fieldsep':',', 'out-outhead':'on',
            'out-height':'ellipsoidal', 'out-outvel':'off', 'out-outstat':'off',
            'inpstr1-type':'tcpcli', 'inpstr2-type':'tcpcli', 'inpstr3-type':'off',
            'inpstr1-path':f'127.0.0.1:{ports[0]}', 'inpstr2-path':f'127.0.0.1:{ports[1]}',
            'inpstr1-format':'rtcm3', 'inpstr2-format':'rtcm3',
            'outstr1-type':'tcpcli', 'outstr1-path':f'127.0.0.1:{ports[2]}', 'outstr1-format':'llh',
            'outstr2-type':'off', 'logstr1-type':'off', 'logstr2-type':'off', 'logstr3-type':'off',
            # Loopback can legitimately be quiet while waiting for ephemerides
            # or a solution; Python enforces freshness and supervises the child.
            'misc-svrcycle':10, 'misc-timeout':0, 'misc-reconnect':1000,
            'misc-buffsize':65536, 'misc-navmsgsel':'all'}
        # RTKLIB does not strip whitespace after '=' (including stream paths).
        return ''.join(f'{k} ={v}\n' for k,v in settings.items())

    def cycle(self):
        servers, clients, buffers = [], {}, {0:bytearray(),1:bytearray(),2:bytearray()}
        process = None
        self.restart.clear()
        with tempfile.TemporaryDirectory(prefix='pftracker-rtk-', dir='/var/run' if os.name=='posix' and Path('/var/run').is_dir() else None) as td:
            try:
                servers = [self.listener() for _ in range(3)]
                path = Path(td)/'solver.conf'
                path.write_text(self.configuration([s.getsockname()[1] for s in servers]))
                path.chmod(0o600)
                process = subprocess.Popen([self.executable,'-n','-s','-o',str(path)], cwd=td,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.process = process
                os.set_blocking(process.stderr.fileno(), False)
                began, stderr = time.monotonic(), b''
                with self.lock:
                    self.state, self.error = 'waiting_solution', ''
                    self.solution = self.solution_at = None
                while not self.stop.is_set() and not self.restart.is_set():
                    if process.poll() is not None:
                        raise OSError('RTKLIB exited with status '+str(process.returncode))
                    if time.monotonic()-began > 15 and not (0 in clients and 1 in clients):
                        raise OSError('RTKLIB local stream connection timeout')
                    readers = servers + ([clients[2]] if 2 in clients else []) + [process.stderr]
                    writers = [clients[i] for i in (0,1) if i in clients and buffers[i]]
                    ready, writable, _ = select.select(readers, writers, [], 0.1)
                    for server in servers:
                        if server not in ready: continue
                        client, _ = server.accept()
                        client.setblocking(False)
                        index = servers.index(server)
                        if index in clients: clients[index].close()
                        clients[index] = client
                        buffers[index].clear()
                        with self.lock:
                            self.connected_inputs=sum(i in clients for i in (0,1))
                    if process.stderr in ready:
                        part = os.read(process.stderr.fileno(), 4096)
                        stderr = (stderr+part)[-2048:]
                        if b'error' in part.lower():
                            with self.lock:
                                self.error = stderr.decode('ascii','replace').strip()[-512:]
                    if 2 in clients and clients[2] in ready:
                        part = clients[2].recv(16384)
                        if not part: raise OSError('RTKLIB solution stream closed')
                        buffers[2].extend(part)
                        while b'\n' in buffers[2]:
                            line, _, remaining = buffers[2].partition(b'\n')
                            buffers[2] = bytearray(remaining)
                            try:
                                sol = parse_solution(line.decode('ascii').strip())
                            except (ValueError, UnicodeError):
                                continue
                            if sol:
                                with self.lock:
                                    if self.solution is None or sol['epoch'] > self.solution['epoch']:
                                        self.solution, self.solution_at = sol, time.monotonic()
                                        self.state, self.error = ('fix' if sol['status']==1 else 'float' if sol['status']==2 else 'no_rtk'), ''
                        if len(buffers[2]) > 8192: raise OSError('Invalid RTKLIB output')
                    for i,q in ((0,self.rover),(1,self.base)):
                        if i not in clients: continue
                        while len(buffers[i]) < 32768:
                            try: buffers[i].extend(q.get_nowait())
                            except queue.Empty: break
                        if clients[i] in writable:
                            n = clients[i].send(buffers[i])
                            del buffers[i][:n]
            finally:
                with self.lock:
                    self.solution = self.solution_at = None
                    self.connected_inputs = 0
                if process:
                    process.terminate()
                    try: process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=2)
                    process.stderr.close()
                self.process = None
                for sock in list(clients.values())+servers: sock.close()

    def run(self):
        while not self.stop.is_set():
            try:
                self.cycle()
            except (OSError, ValueError) as error:
                self.reset()
                with self.lock:
                    self.state, self.error = 'error', str(error)
                self.stop.wait(2)
            if not self.restart.is_set(): self.stop.wait(2)

    def close(self):
        self.ntrip.close()
        self.worker.join(timeout=7)
        self.network_worker.join(timeout=7)
