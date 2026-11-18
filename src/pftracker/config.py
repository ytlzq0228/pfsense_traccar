import json
import math
import os
import re
from urllib.parse import urlsplit

DEFAULTS = dict(enabled=False, source='ch340', usb_selector='', baud=230400,
                serial_path='/dev/cuaU0', server_url='', device_id='', traccar_enabled=True,
                aprs_enabled=False, aprs_host='china.aprs2.net', aprs_port=14580,
                aprs_callsign='', aprs_passcode='', aprs_symbol_table='/', aprs_symbol='>',
                aprs_comment='', aprs_moving_interval=30, aprs_stopped_interval=300,
                aprs_timeout=5, aprs_upload_interval=5,
                moving_interval=1, stopped_interval=120, speed_threshold=3,
                speed_hysteresis=1, move_delay=3, stop_delay=15,
                fix_timeout=5, min_sat=4, max_hdop=5,
                queue_dir='/conf/traccar', max_points=100000, queue_mb=128,
                reserve_mb=64, http_timeout=3, upload_rate=5,
                log_mb=2, log_backups=3, log_positions=True, log_nmea=False,
                msm_enabled=False, msm_mode='msm7', rtk_enabled=False,
                ntrip_host='rtk.ntrip.qxwz.com', ntrip_port=8002, ntrip_mountpoint='AUTO',
                ntrip_username='', ntrip_password='', rtk_allow_float=True,
                rtk_max_age=10, rtk_max_sigma=3)
RANGES = dict(baud=(4800,230400), moving_interval=(1,3600), stopped_interval=(1,86400),
              speed_threshold=(0.1,200), speed_hysteresis=(0,50), move_delay=(0,120), stop_delay=(0,600),
              fix_timeout=(2,60), min_sat=(0,64), max_hdop=(0.5,99), max_points=(10,1000000),
              queue_mb=(1,4096), reserve_mb=(16,4096), http_timeout=(1,30), upload_rate=(1,50),
              log_mb=(1,20), log_backups=(1,10), aprs_port=(1,65535),
              aprs_moving_interval=(30,3600), aprs_stopped_interval=(30,86400),
              aprs_timeout=(1,30), aprs_upload_interval=(5,300), ntrip_port=(1,65535),
              rtk_max_age=(1,60), rtk_max_sigma=(0.01,50))
INTEGERS = {'baud','min_sat','max_points','queue_mb','reserve_mb','log_mb','log_backups','aprs_port','ntrip_port'}


def validate(data):
    config = {**DEFAULTS, **data}
    unknown = set(data)-set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown settings: ' + ', '.join(sorted(unknown)))
    for key in ('enabled','traccar_enabled','aprs_enabled','log_positions','log_nmea',
                'msm_enabled','rtk_enabled','rtk_allow_float'):
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
    if config['enabled'] and config['traccar_enabled'] and (not config['device_id'] or not config['server_url']):
        raise ValueError('Server URL and device ID are required when enabled')
    if not isinstance(config['aprs_host'], str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', config['aprs_host']):
        raise ValueError('APRS host must be a hostname or IPv4 address without port')
    call = config['aprs_callsign']
    if not isinstance(call, str) or (call and (len(call) > 9 or not re.fullmatch(r'[A-Z0-9]{3,6}(?:-(?!0$)[A-Z0-9]{1,2})?', call))):
        raise ValueError('APRS callsign must be uppercase, optionally followed by SSID; maximum 9 characters')
    password = config['aprs_passcode']
    if not isinstance(password, str) or (password and (not re.fullmatch(r'[0-9]{1,5}', password) or int(password) > 32767)):
        raise ValueError('APRS passcode must be an integer from 0 to 32767')
    if not isinstance(config['aprs_symbol_table'], str) or not re.fullmatch(r'[/\\0-9A-Z]', config['aprs_symbol_table']):
        raise ValueError('APRS symbol table must be /, backslash, or an overlay 0-9/A-Z')
    if not isinstance(config['aprs_symbol'], str) or not re.fullmatch(r'[!-~]', config['aprs_symbol']):
        raise ValueError('APRS symbol must be one printable ASCII character')
    comment = config['aprs_comment']
    if not isinstance(comment, str) or len(comment.encode('utf-8')) > 160 or any(not ch.isprintable() for ch in comment):
        raise ValueError('APRS comment must be printable text, up to 160 UTF-8 bytes, without line breaks')
    if config['aprs_enabled'] and (not call or not password):
        raise ValueError('APRS callsign and passcode are required when APRS is enabled')
    if config['msm_mode'] not in ('msm4','msm7'):
        raise ValueError('MSM mode must be msm4 or msm7')
    if not isinstance(config['ntrip_host'], str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', config['ntrip_host']):
        raise ValueError('NTRIP host must be a hostname or IPv4 address without port')
    if not isinstance(config['ntrip_mountpoint'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',config['ntrip_mountpoint']):
        raise ValueError('Invalid NTRIP mountpoint')
    for key in ('ntrip_username','ntrip_password'):
        value=config[key]
        if not isinstance(value,str) or len(value.encode('utf8'))>128 or any(ord(ch)<32 or ord(ch)==127 for ch in value):
            raise ValueError('Invalid '+key)
    if ':' in config['ntrip_username']:
        raise ValueError('NTRIP username must not contain colon')
    if config['rtk_enabled'] and (not config['msm_enabled'] or not config['ntrip_username'] or not config['ntrip_password']):
        raise ValueError('RTK requires MSM output and NTRIP credentials')
    return config


def load(path):
    with open(path) as f:
        return validate(json.load(f))
