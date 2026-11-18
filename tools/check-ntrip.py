#!/usr/bin/env python3
"""Read-only caster preflight using the running service's real GPS status.

Does not open USB, send simulated positions, or print credentials.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from pftracker.config import DEFAULTS,validate
from pftracker.ntrip import Ntrip
from pftracker.rtcm import sentence


def gga_from_status(status):
    g=status.get('gps',{});f=g.get('fix') or {}
    if not g.get('valid') or g.get('fix_age',99)>5 or time.time()-status.get('updated',0)>10:
        return None
    def degrees(value):
        value=abs(value);d=int(value)
        return d*100+(value-d)*60
    body='GPGGA,%s,%010.6f,%s,%011.6f,%s,1,%02d,%.2f,%.3f,M,,M,,' % (
        datetime.fromtimestamp(f['epoch'],timezone.utc).strftime('%H%M%S.%f')[:-3],
        degrees(f['lat']),'N' if f['lat']>=0 else 'S',
        degrees(f['lon']),'E' if f['lon']>=0 else 'W',
        f['sat'],f['hdop'],f['altitude'] or 0)
    return sentence(body).decode().strip()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('credentials',type=Path)
    p.add_argument('--seconds',type=int,default=40)
    a=p.parse_args()
    c=validate({**DEFAULTS,**json.loads(a.credentials.read_text())})
    stop=threading.Event();types={}
    def received(frame):
        kind=str((frame[3]<<4)|(frame[4]>>4));types[kind]=types.get(kind,0)+1
    client=Ntrip(c,stop,received)
    thread=threading.Thread(target=client.run,daemon=True)
    end=time.monotonic()+a.seconds
    thread.start()
    try:
        while time.monotonic()<end:
            try:client.set_gga(gga_from_status(json.loads(Path('/var/run/traccar-status.json').read_text())))
            except (OSError,ValueError,KeyError):client.set_gga(None)
            if len(types)>=3 and client.snapshot()['valid_frames']>=10:break
            time.sleep(.5)
        result=client.snapshot();result['types']=types
    finally:
        stop.set();client.close();thread.join(7)
    print(json.dumps(result))
    raise SystemExit(0 if result['valid_frames'] else 1)


if __name__=='__main__':main()
