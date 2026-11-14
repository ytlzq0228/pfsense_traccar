import json
import math
import os
import re
from urllib.parse import urlsplit

DEFAULTS = dict(enabled=False, source='ch340', usb_selector='', baud=115200,
                serial_path='/dev/cuaU0', server_url='', device_id='',
                moving_interval=1, stopped_interval=120, speed_threshold=3,
                speed_hysteresis=1, move_delay=3, stop_delay=15,
                fix_timeout=5, min_sat=4, max_hdop=5,
                queue_dir='/conf/traccar', max_points=100000, queue_mb=128,
                reserve_mb=64, http_timeout=3, upload_rate=5,
                log_mb=2, log_backups=3, log_positions=True, log_nmea=False)
RANGES = dict(baud=(4800,230400), moving_interval=(1,3600), stopped_interval=(1,86400),
              speed_threshold=(0.1,200), speed_hysteresis=(0,50), move_delay=(0,120), stop_delay=(0,600),
              fix_timeout=(2,60), min_sat=(0,64), max_hdop=(0.5,99), max_points=(10,1000000),
              queue_mb=(1,4096), reserve_mb=(16,4096), http_timeout=(1,30), upload_rate=(1,50),
              log_mb=(1,20), log_backups=(1,10))
INTEGERS = {'baud','min_sat','max_points','queue_mb','reserve_mb','log_mb','log_backups'}


def validate(data):
    config = {**DEFAULTS, **data}
    unknown = set(data)-set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown settings: ' + ', '.join(sorted(unknown)))
    for key in ('enabled','log_positions','log_nmea'):
        if type(config[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    for key, (low, high) in RANGES.items():
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('%s must be between %s and %s' % (key, low, high))
        if key in INTEGERS and not isinstance(value, int):
            raise ValueError(key + ' must be integer')
    if config['baud'] not in (4800,9600,19200,38400,57600,115200,230400):
        raise ValueError('Unsupported baud rate')
    if config['speed_hysteresis'] >= config['speed_threshold']:
        raise ValueError('Speed hysteresis must be lower than movement threshold')
    if config['source'] not in ('ch340','serial'):
        raise ValueError('Source must be ch340 or serial')
    if not re.fullmatch(r'(ugen\d+\.\d+)?', config['usb_selector']):
        raise ValueError('USB selector must be empty or ugenBUS.ADDRESS')
    if not re.fullmatch(r'/dev/cua[A-Za-z0-9._-]+', config['serial_path']):
        raise ValueError('Serial path must be a /dev/cua device')
    path = config['queue_dir']
    if not re.fullmatch(r'/(?:conf|mnt)/[A-Za-z0-9_./-]+', path) or '..' in path.split('/') or path.endswith('/'):
        raise ValueError('Queue directory must be a dedicated absolute subdirectory of /conf or /mnt')
    if config['server_url']:
        u = urlsplit(config['server_url'])
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError('Server URL must be HTTP(S), without embedded credentials or fragment')
        _ = u.port
        if any(ord(c) <= 32 for c in config['server_url']):
            raise ValueError('Invalid server URL')
    if config['device_id'] and not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', config['device_id']):
        raise ValueError('Invalid device ID')
    if config['enabled'] and (not config['device_id'] or not config['server_url']):
        raise ValueError('Server URL and device ID are required when enabled')
    return config


def load(path):
    with open(path) as f:
        return validate(json.load(f))
