"""Bounded NMEA/RTCM3 demultiplexer. Binary payload is never parsed as NMEA."""
import time


def _crc_table():
    table=[]
    for value in range(256):
        crc=value<<16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc & 0xffffff)
    return tuple(table)


CRC_TABLE=_crc_table()


def crc24q(data):
    crc=0
    for value in data:
        crc=((crc<<8)&0xffffff)^CRC_TABLE[(crc>>16)^value]
    return crc


def sentence(body):
    checksum = 0
    for value in body.encode('ascii'):
        checksum ^= value
    return ('$%s*%02X\r\n' % (body, checksum)).encode('ascii')


class MixedStream:
    def __init__(self):
        self.buffer = bytearray()
        self.frames = self.bad_crc = self.discarded = 0
        self.types = {}
        self.last_msm = None

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        self.buffer.extend(data)
        messages = []
        while self.buffer:
            if self.buffer[0] not in (0x24, 0xd3):
                self.discarded += 1
                del self.buffer[0]
                continue
            if self.buffer[0] == 0x24:
                end = self.buffer.find(b'\n')
                binary = self.buffer.find(b'\xd3')
                if binary >= 0 and (end < 0 or binary < end):
                    self.discarded += binary
                    del self.buffer[:binary]
                    continue
                if end < 0:
                    if len(self.buffer) <= 512:
                        break
                    self.discarded += 1
                    del self.buffer[0]
                    continue
                messages.append(('nmea', bytes(self.buffer[:end+1])))
                del self.buffer[:end+1]
                continue
            if len(self.buffer) < 3:
                break
            length = ((self.buffer[1] & 3) << 8) | self.buffer[2]
            if self.buffer[1] & 0xfc or length < 2:
                self.discarded += 1
                del self.buffer[0]
                continue
            total = length + 6
            if len(self.buffer) < total:
                break
            frame = bytes(self.buffer[:total])
            if crc24q(frame[:-3]) != int.from_bytes(frame[-3:], 'big'):
                self.bad_crc += 1
                del self.buffer[0]
                continue
            del self.buffer[:total]
            kind = (frame[3] << 4) | (frame[4] >> 4)
            self.frames += 1
            info = self.types.setdefault(kind, {'count': 0})
            info.update(count=info['count']+1, at=now)
            if 1071 <= kind <= 1127 and kind % 10 in (4, 5, 6, 7) and length >= 22:
                bits = ''.join(f'{x:08b}' for x in frame[3:-3])
                info.update(satellites=bits[73:137].count('1'), signals=bits[137:169].count('1'))
                info['system'] = {107:'GPS',108:'GLONASS',109:'Galileo',110:'SBAS',111:'QZSS',112:'BeiDou'}.get(kind//10)
                self.last_msm = now
            messages.append(('rtcm', frame))
        return messages

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        return dict(valid_frames=self.frames, bad_crc=self.bad_crc, discarded_bytes=self.discarded,
                    msm_age=None if self.last_msm is None else max(0, now-self.last_msm),
                    types={str(k): {**{n:v for n,v in item.items() if n != 'at'},
                                    'age': max(0, now-item['at'])} for k,item in self.types.items()})
