"""Explicit LC76G maintenance. Stop the service before changing GNSS baud rate."""
import argparse
import fcntl
import time
from pathlib import Path

from .nmea import NMEA
from .rtcm import MixedStream, sentence
from .usb import CH340


def collect(source, seconds):
    framing, parser, lines = MixedStream(), NMEA(), []
    until = time.monotonic()+seconds
    while time.monotonic() < until:
        for kind,data in framing.feed(source.read()):
            if kind=='nmea': lines.extend(parser.feed(data,time.monotonic()))
    return lines


def verify(source, baud, seconds=8):
    collect(source, 1)
    source.write(sentence('PAIR865,0,0'))
    lines = collect(source, seconds)
    epochs = set(line.split(',')[1] for line in lines if line.startswith(('$GNRMC,','$GPRMC,')))
    configured = any(line.startswith(f'$PAIR865,{baud}*') for line in lines)
    return len(epochs)>=2 and configured


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baud',type=int,choices=(115200,230400),required=True)
    p.add_argument('--current-baud',type=int,choices=(115200,230400),default=115200)
    p.add_argument('--selector',default='')
    p.add_argument('--queue-dir',default='/conf/traccar')
    a = p.parse_args()
    with (Path(a.queue_dir)/'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Stop Traccar before GNSS maintenance; another reader owns the service lock')
        source = CH340(a.current_baud,a.selector)
        try:
            if not verify(source,a.current_baud,5):
                raise SystemExit('Current baud/NMEA could not be verified; no GNSS settings changed')
            if a.current_baud==a.baud:
                print('Verified GNSS baud',a.baud)
                return
            source.write(sentence(f'PAIR864,0,0,{a.baud}'))
            replies = collect(source,2)
            if not any(line.startswith('$PAIR001,864,0*') for line in replies):
                raise SystemExit('GNSS did not acknowledge baud setting')
            source.write(sentence('PAIR023'))
            collect(source,0.5)
        finally:
            source.close()
        time.sleep(2)
        source=CH340(a.baud,a.selector)
        try:
            if not verify(source,a.baud,12):
                raise SystemExit('New baud not verified; keep service stopped and inspect GNSS before restart')
        finally:
            source.close()
        print('Verified LC76G software reboot and active baud',a.baud)


if __name__=='__main__':
    main()
