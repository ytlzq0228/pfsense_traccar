#!/usr/bin/env python3
"""Compare RTKLIB profiles against a private, timestamped dual-input recording.

Only loopback native solvers are used. No USB, NTRIP login or position upload.
Input records: network byte order double monotonic seconds, byte source,
uint32 length, payload. Sources 0/1 are rover/base RTCM; 2 is original CSV.
"""
import argparse
from collections import Counter
import json
import inspect
from pathlib import Path
import statistics
import struct
import threading
import time

from pftracker.config import DEFAULTS
from pftracker.rtk import RTK


class Replay(RTK):
    def __init__(self, executable, extra):
        super().__init__(DEFAULTS, threading.Event(), executable)
        self.extra=extra
        self.results=[]
        self.seen=None

    def configuration(self, ports):
        return super().configuration(ports)+''.join(f'{k} ={v}\n' for k,v in self.extra.items())

    def collect(self, elapsed):
        s=self.snapshot()
        q=s['solution']
        if q and q['epoch']!=self.seen:
            self.results.append(dict(elapsed=elapsed,**q))
            self.seen=q['epoch']

    def summary(self):
        qualities=Counter(x['status'] for x in self.results)
        good=[]
        for x in self.results:
            if x['status'] in (1,2) and x['sat']>=4 and x['sigma_horizontal']<=3 and x['sigma_up']<=6 and abs(x['age'])<=10 and (x['status']==2 or x['ratio']>=3):
                good.append(x)
        run=longest=0;previous=None
        for x in self.results:
            if x in good and x['status']==1:
                run=run+1 if previous and previous['status']==1 and x['epoch']-previous['epoch']<=1.01 else 1
                longest=max(longest,run)
            else:run=0
            previous=x
        return dict(epochs=len(self.results),quality=dict(qualities),qualified=dict(Counter(x['status'] for x in good)),
                    longest_fix_epochs=longest,
                    age_median=statistics.median(x['age'] for x in self.results) if self.results else None)


def records(path,seconds):
    data=path.read_bytes();offset=0;first=None
    while offset+13<=len(data):
        at,source,size=struct.unpack_from('!dBI',data,offset);offset+=13
        if size>16384 or source not in (0,1,2):raise ValueError('Invalid capture record')
        if offset+size>len(data):break
        frame=data[offset:offset+size];offset+=size
        if first is None:first=at
        if at-first>seconds:break
        if source in (0,1):
            if len(frame)<8 or frame[0]!=0xd3:raise ValueError('Invalid RTCM input record')
            yield at-first,source,frame


def main():
    p=argparse.ArgumentParser()
    p.add_argument('capture',type=Path)
    p.add_argument('--seconds',type=int,default=180)
    p.add_argument('--executable',default='/usr/local/libexec/pftracker-rtkrcv')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--profiles',default='baseline,raim,dynamics,dynamics_raim,dynamics_raim_delay')
    a=p.parse_args()
    profiles={
        'baseline':({'pos1-dynamics':'off'},0),
        'raim':({'pos1-dynamics':'off','pos1-posopt5':'on'},0),
        'dynamics':({'pos1-dynamics':'on'},0),
        'dynamics_raim':({'pos1-dynamics':'on','pos1-posopt5':'on'},0),
        'dynamics_raim_delay':({'pos1-dynamics':'on','pos1-posopt5':'on'},.65),
        'gps_only':({'pos1-dynamics':'on','pos1-navsys':1},0),
        'gps_bds':({'pos1-dynamics':'on','pos1-navsys':33},0),
        'snr35':({'pos1-dynamics':'on','pos1-snrmask_L1':','.join(['35']*9)},0),
        'elevation20':({'pos1-dynamics':'on','pos1-elmask':20},0),
        'lock30':({'pos1-dynamics':'on','pos2-arlockcnt':30},0),
        'lock60':({'pos1-dynamics':'on','pos2-arlockcnt':60},0),
        'ar_elevation25':({'pos1-dynamics':'on','pos2-arelmask':25},0),
        'process_standard':({'pos1-dynamics':'on','stats-prnaccelh':1,'stats-prnaccelv':.1},0),
        'process_vehicle':({'pos1-dynamics':'on','stats-prnaccelh':3,'stats-prnaccelv':1},0),
        'process_loose':({'pos1-dynamics':'on','stats-prnaccelh':10,'stats-prnaccelv':10},0),
    }
    chosen=a.profiles.split(',')
    if any(name not in profiles for name in chosen):raise ValueError('Unknown replay profile')
    profiles={name:profiles[name] for name in chosen}
    recorded=list(records(a.capture,a.seconds))
    if not recorded:raise ValueError('Capture contains no RTCM records')
    if 'self.diag' in inspect.getsource(RTK.__init__):
        raise RuntimeError('Replay requires clean application source, without temporary capture hooks')
    # Read the immutable capture before starting any replay instances.
    engines={name:Replay(a.executable,extra) for name,(extra,delay) in profiles.items()}
    events=[]
    for at,source,frame in recorded:
        kind=(frame[3]<<4)|(frame[4]>>4)
        for name,(_,delay) in profiles.items():
            late=delay if source==0 and 1071<=kind<=1127 else 0
            events.append((at+late,name,source,frame))
    events.sort(key=lambda x:x[0])
    try:
        for engine in engines.values():engine.worker.start()
        until=time.monotonic()+10
        while time.monotonic()<until and any(x.snapshot()['connected_inputs']!=2 for x in engines.values()):time.sleep(.05)
        if any(x.snapshot()['connected_inputs']!=2 for x in engines.values()):raise RuntimeError('Native input startup failed')
        began=time.monotonic()
        for at,name,source,frame in events:
            while time.monotonic()-began<at:
                for e in engines.values():e.collect(time.monotonic()-began)
                time.sleep(min(.02,max(0,at-(time.monotonic()-began))))
            e=engines[name]
            if source==0:e.feed_rover(frame)
            else:e.feed(e.base,frame)
        time.sleep(1)
        for e in engines.values():e.collect(time.monotonic()-began)
        result={name:dict(summary=e.summary(),solutions=e.results) for name,e in engines.items()}
        a.output.write_text(json.dumps(result,indent=2));a.output.chmod(0o600)
        print(json.dumps({name:x['summary'] for name,x in result.items()}),flush=True)
    finally:
        for e in engines.values():e.stop.set()
        for e in engines.values():e.worker.join(timeout=7)


if __name__=='__main__':main()
