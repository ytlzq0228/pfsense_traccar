"""Strict NMEA epoch assembly. Never promote a stale coordinate to a new fix."""
from datetime import datetime, timezone
import math


def number(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('Non-finite NMEA number')
    return n


def coordinate(value, direction, latitude):
    raw = number(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    limit = 90 if latitude else 180
    if direction not in ('NS' if latitude else 'EW') or raw < 0 or minutes >= 60:
        raise ValueError('Invalid coordinate')
    result = degrees + minutes / 60
    if result > limit:
        raise ValueError('Coordinate outside range')
    return -result if direction in 'SW' else result


def tod(value):
    if len(value) < 6:
        raise ValueError('Missing UTC time')
    h, m, s = int(value[:2]), int(value[2:4]), number(value[4:])
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError('Invalid UTC time')
    return h * 3600 + m * 60 + s


def system_name(talker, system=''):
    return {'1': 'GPS', '2': 'GLONASS', '3': 'Galileo', '4': 'BeiDou', '5': 'QZSS', '6': 'NavIC'}.get(system,
           {'GP': 'GPS', 'GL': 'GLONASS', 'GA': 'Galileo', 'GB': 'BeiDou', 'BD': 'BeiDou', 'GQ': 'QZSS', 'GI': 'NavIC'}.get(talker, talker))


class NMEA:
    def __init__(self):
        self.buffer = b''
        self.rmc = self.gga = self.fix = None
        self.last_line = None
        self.last_fix = None
        self.valid = False
        self.reason = 'waiting for NMEA'
        self.valid_lines = self.bad_lines = 0
        self.satellites = {}
        self.gsv_groups = {}
        self.used = {}
        self.fix_mode = None
        self.mode_at = None

    def feed(self, chunk, now):
        self.buffer += chunk
        lines = []
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            if len(raw) > 512:
                self.bad_lines += 1
                continue
            try:
                line = raw.decode('ascii').strip()
                body, check = line[1:].split('*')
                xor = 0
                for char in body:
                    xor ^= ord(char)
                if not line.startswith('$') or len(check) != 2 or xor != int(check, 16):
                    raise ValueError('checksum')
                self.parse(body.split(','), now)
                self.last_line = now
                self.valid_lines += 1
                lines.append(line)
            except (ValueError, IndexError, OverflowError):
                self.bad_lines += 1
        if len(self.buffer) > 1024:
            self.buffer = b''
            self.bad_lines += 1
        return lines

    def parse(self, f, now):
        kind, talker = f[0][-3:], f[0][:2]
        if kind == 'RMC':
            if f[2] != 'A' or (len(f) > 12 and f[12] in ('N', 'E', 'S')):
                self.rmc = None
                self.valid, self.reason = False, 'RMC: no valid fix'
                return
            t = tod(f[1])
            if len(f[9]) != 6:
                raise ValueError('Missing GNSS date')
            year = 2000 + int(f[9][4:6])
            if year < 2020:
                raise ValueError('Untrusted GNSS date')
            day = datetime(year, int(f[9][2:4]), int(f[9][:2]), tzinfo=timezone.utc)
            speed = number(f[7]) if f[7] else None
            bearing = number(f[8]) if f[8] else None
            if speed is not None and not 0 <= speed <= 1000:
                raise ValueError('Invalid speed')
            if bearing is not None and not 0 <= bearing <= 360:
                raise ValueError('Invalid bearing')
            self.rmc = dict(t=t, at=now, epoch=day.timestamp()+t,
                            lat=coordinate(f[3], f[4], True), lon=coordinate(f[5], f[6], False),
                            speed=speed, bearing=bearing)
            self.assemble(now)
        elif kind == 'GGA':
            quality = int(f[6] or '0')
            if quality not in (1, 2, 4, 5):
                self.gga = None
                self.valid, self.reason = False, 'GGA: no GNSS fix'
                return
            self.gga = dict(t=tod(f[1]), at=now, quality=quality, sat=int(f[7] or '0'),
                            hdop=number(f[8]) if f[8] else None,
                            altitude=number(f[9]) if f[9] and f[10] == 'M' else None,
                            geoid_separation=number(f[11]) if len(f)>12 and f[11] and f[12]=='M' else None)
            self.assemble(now)
        elif kind == 'GSA':
            self.fix_mode, self.mode_at = int(f[2]), now
            constellation = system_name(talker, f[18] if len(f) > 18 else '')
            self.used[constellation] = (now, set(int(v) for v in f[3:15] if v))
            if self.fix_mode == 1:
                self.valid, self.reason = False, 'GSA: no fix'
        elif kind == 'GSV':
            total, part = int(f[1]), int(f[2])
            if not 1 <= part <= total <= 16:
                raise ValueError('Invalid GSV sequence')
            fields = f[4:]
            signal = fields[-1] if len(fields) % 4 == 1 else ''
            constellation = system_name(talker)
            key = (constellation, signal)
            if part == 1:
                self.gsv_groups[key] = [now, total, {}]
            group = self.gsv_groups.get(key)
            if group is None or group[1] != total or now-group[0] > 5:
                return
            entries = []
            for i in range(0, len(fields)-3, 4):
                if not fields[i]:
                    continue
                entries.append(dict(system=constellation, prn=int(fields[i]), signal=signal,
                                    elevation=number(fields[i+1]) if fields[i+1] else None,
                                    azimuth=number(fields[i+2]) if fields[i+2] else None,
                                    snr=number(fields[i+3]) if fields[i+3] else None, at=now))
            group[2][part] = entries
            if len(group[2]) == total:
                self.satellites[key] = [e for j in range(1, total+1) for e in group[2][j]]
                del self.gsv_groups[key]

    def assemble(self, now):
        r, g = self.rmc, self.gga
        if not r or not g or abs(r['t']-g['t']) > 0.05 or now-min(r['at'], g['at']) > 2:
            return
        if self.fix_mode == 1 and self.mode_at is not None and now-self.mode_at < 3:
            return
        if self.fix and r['epoch'] <= self.fix['epoch']:
            return
        self.fix = {**r, **{k: g[k] for k in ('quality', 'sat', 'hdop', 'altitude', 'geoid_separation')}}
        self.last_fix, self.valid, self.reason = now, True, 'valid fix'

    def snapshot(self, now, stale=5, max_hdop=5, min_sat=4):
        valid, reason = self.valid, self.reason
        age = None if self.last_fix is None else max(0, now-self.last_fix)
        if self.last_line is None or now-self.last_line > stale:
            valid, reason = False, 'NMEA data stale / disconnected'
        elif age is None or age > stale:
            valid, reason = False, 'position stale'
        elif valid and self.fix:
            if self.fix['sat'] < min_sat or self.fix['hdop'] is None or not 0 < self.fix['hdop'] <= max_hdop:
                valid, reason = False, 'weak fix: satellite count / HDOP'
        satellites = []
        for entries in self.satellites.values():
            for sat in entries:
                if now-sat['at'] > 15:
                    continue
                sat = dict(sat)
                used = self.used.get(sat['system'])
                sat['used'] = bool(used and now-used[0] <= 15 and sat['prn'] in used[1])
                sat.pop('at')
                satellites.append(sat)
        return dict(valid=valid, reason=reason, fix=self.fix, fix_age=age,
                    satellites=satellites, valid_lines=self.valid_lines, bad_lines=self.bad_lines)


class Sampler:
    def __init__(self, config):
        self.config = config
        self.moving = False
        self.candidate = None
        self.candidate_since = 0
        self.last_sample = None
        self.was_valid = False
        self.last_epoch = None

    def choose(self, snapshot, now):
        if not snapshot['valid']:
            self.was_valid = False
            self.candidate = None
            return None
        fix = snapshot['fix']
        kmh = fix['speed'] * 1.852 if fix['speed'] is not None else None
        # Unknown speed uses the faster interval rather than silently declaring a stop.
        if kmh is None:
            target = True
        elif self.moving:
            target = kmh > max(0, self.config['speed_threshold']-self.config['speed_hysteresis'])
        else:
            target = kmh >= self.config['speed_threshold']
        changed = False
        if target != self.moving:
            if self.candidate != target:
                self.candidate, self.candidate_since = target, now
            delay = self.config['move_delay'] if target else self.config['stop_delay']
            if now-self.candidate_since >= delay:
                self.moving, self.candidate, changed = target, None, True
        else:
            self.candidate = None
        interval = self.config['moving_interval'] if self.moving or kmh is None else self.config['stopped_interval']
        due = not self.was_valid or changed or self.last_sample is None or now-self.last_sample >= interval
        self.was_valid = True
        if due and (self.last_epoch is None or fix['epoch'] > self.last_epoch):
            self.last_sample, self.last_epoch = now, fix['epoch']
            return fix
        return None
