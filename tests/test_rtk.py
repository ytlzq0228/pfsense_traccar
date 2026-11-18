import socket
import threading
import time
import unittest
from unittest.mock import patch

from pftracker.config import DEFAULTS,validate
from pftracker.rtcm import MixedStream,crc24q,sentence
from pftracker.ntrip import Chunked,response,Ntrip
from pftracker.rtk import parse_solution,select_position,RTK
from pftracker.nmea import Sampler


def frame(kind,body=b''):
    payload=bytes([kind>>4,(kind&15)<<4])+body
    head=b'\xd3'+len(payload).to_bytes(2,'big')+payload
    return head+crc24q(head).to_bytes(3,'big')


class FramingTests(unittest.TestCase):
    def test_crc_known_check_value(self):
        self.assertEqual(crc24q(b'123456789'),0xCDE703)

    def test_binary_containing_nmea_and_every_fragment_boundary(self):
        binary=frame(1019,b'\n$GNGGA,garbage*00\n\xd3\x00')
        ascii_line=sentence('GNRMC,120000,V,,,,,,,181126,,,N')
        stream=ascii_line+binary+ascii_line
        for boundary in range(len(stream)+1):
            p=MixedStream()
            messages=p.feed(stream[:boundary],1)+p.feed(stream[boundary:],2)
            self.assertEqual(messages,[('nmea',ascii_line),('rtcm',binary),('nmea',ascii_line)])
            self.assertEqual(p.frames,1)

    def test_corruption_recovers_and_memory_bounded(self):
        p=MixedStream()
        good=frame(1042,b'abcd')
        bad=bytearray(good);bad[-1]^=1
        self.assertEqual(p.feed(bytes(bad)+good),[('rtcm',good)])
        self.assertEqual(p.bad_crc,1)
        p.feed(b'$'+b'x'*10000)
        self.assertLessEqual(len(p.buffer),512)


class NtripTests(unittest.TestCase):
    def test_loopback_stream_and_disconnect_invalidates_corrections(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
        stop=threading.Event();release=threading.Event();received=[];requests=[];errors=[]
        def caster():
            try:
                listener.settimeout(5)
                sock,_=listener.accept()
                with sock:
                    sock.settimeout(5);request=b''
                    while b'\r\n\r\n' not in request:request+=sock.recv(4096)
                    requests.append(request)
                    sock.sendall(b'ICY 200 OK\r\n'+frame(1005)+frame(1074))
                    release.wait(5)
            except OSError as error:errors.append(error)
        server=threading.Thread(target=caster);server.start()
        client=Ntrip({**DEFAULTS,'ntrip_host':'127.0.0.1','ntrip_port':listener.getsockname()[1],
                      'ntrip_username':'test','ntrip_password':'test-only'},stop,received.append)
        client.set_gga(sentence('GNGGA,120000,4000,N,11600,E,1,10,1,50,M,-6,M,,').decode().strip())
        def consume():
            try:client.stream('AUTO')
            except OSError:pass
        worker=threading.Thread(target=consume);worker.start()
        try:
            until=time.monotonic()+4
            while len(received)<2 and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(len(received),2)
            self.assertIsNotNone(client.snapshot()['correction_age'])
            self.assertIn(b'Ntrip-GGA: $GPGGA',requests[0])
            self.assertNotIn('test-only',str(client.snapshot()))
            release.set();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(client.snapshot()['correction_age'])
        finally:
            stop.set();release.set();client.close();worker.join(3);server.join(3);listener.close()
        self.assertFalse(errors)

    def test_icy_and_http_and_secret_safe_errors(self):
        for data,expected in [(b'ICY 200 OK\r\n'+frame(1005),False),
                              (b'HTTP/1.1 200 OK\r\nContent-Type: gnss/data\r\nTransfer-Encoding: chunked\r\n\r\n',True)]:
            a,b=socket.socketpair()
            try:
                a.sendall(data)
                body,chunked=response(b)
                self.assertEqual(chunked,expected)
                if not chunked:self.assertEqual(body,frame(1005))
            finally:a.close();b.close()
        a,b=socket.socketpair()
        try:
            a.sendall(b'HTTP/1.1 401 secret-password-reflection\r\n\r\n')
            with self.assertRaisesRegex(OSError,'^NTRIP HTTP 401$'):response(b)
        finally:a.close();b.close()

    def test_chunked_fragmentation(self):
        p=Chunked();data=b'3\r\nabc\r\n2;foo=bar\r\nde\r\n'
        result=b''.join(p.feed(bytes([x])) for x in data)
        self.assertEqual(result,b'abcde')
        with self.assertRaisesRegex(ValueError,'^Invalid NTRIP chunk size$'):
            Chunked().feed(b'secret-reflection\r\n')


class FallbackTests(unittest.TestCase):
    def test_sampler_never_replays_older_epoch_after_source_change(self):
        sampler=Sampler(DEFAULTS)
        gga={'valid':True,'fix':{'epoch':100,'speed':None}}
        self.assertIsNotNone(sampler.choose(gga,0))
        self.assertIsNone(sampler.choose({'valid':True,'fix':{'epoch':99,'speed':None}},10))
        self.assertIsNotNone(sampler.choose({'valid':True,'fix':{'epoch':101,'speed':None}},11))

    def test_native_configuration_has_no_space_in_values_or_secrets(self):
        rtk=RTK({**DEFAULTS,'ntrip_password':'private-test'},threading.Event())
        conf=rtk.configuration([1234,1235,1236])
        self.assertIn('inpstr1-path =127.0.0.1:1234\n',conf)
        self.assertIn('pos1-dynamics =on\n',conf)
        self.assertNotIn('= ',conf)
        self.assertNotIn('private-test',conf)

    def setUp(self):
        self.gga=dict(valid=True,reason='valid fix',fix_age=0,
                      fix=dict(epoch=1795000000,lat=40.,lon=116.,quality=1,sat=12,hdop=.8,
                               speed=0,bearing=0,altitude=50,geoid_separation=-6))
        self.sol=dict(epoch=1795000000,lat=40.0001,lon=116.0001,ellipsoid_height=44.,
                      status=1,sat=10,sigma_horizontal=.02,sigma_up=.04,age=.5,ratio=5)

    def choose(self,sol=None,sa=.1,ca=.5,ma=.1,c=None):
        return select_position(self.gga,self.sol if sol is None else sol,sa,ca,ma,c or DEFAULTS)

    def test_good_rtk_and_altitude_reference(self):
        out=self.choose()
        self.assertTrue(out['rtk_used']);self.assertEqual(out['fix']['quality'],4)
        self.assertEqual(out['fix']['altitude'],50.)
        self.assertEqual(self.gga['fix']['quality'],1)

    def test_stale_disconnect_poor_float_and_spp_fall_back(self):
        cases=[self.choose(sa=10),self.choose(ca=None),self.choose(ma=10),
               self.choose(sol={**self.sol,'status':5}),
               self.choose(sol={**self.sol,'sigma_horizontal':30}),
               self.choose(sol={**self.sol,'ratio':1}),
               self.choose(sol={**self.sol,'age':30}),
               self.choose(sol={**self.sol,'epoch':1795000100}),
               self.choose(sol={**self.sol,'status':2},c={**DEFAULTS,'rtk_allow_float':False})]
        for out in cases:
            self.assertEqual(out['source'],'gga');self.assertTrue(out['valid'])
            self.assertEqual(out['fix'],self.gga['fix'])

    def test_no_fake_position_when_both_invalid(self):
        gga={**self.gga,'valid':False,'reason':'no fix'}
        out=select_position(gga,None,None,None,None,DEFAULTS)
        self.assertFalse(out['valid'])

    def test_solution_parse_and_config_validation(self):
        line='2026/11/18 10:00:00.000,40,116,44,1,10,.02,.03,.04,0,0,0,.5,5'
        self.assertEqual(parse_solution(line)['status'],1)
        with self.assertRaises(ValueError):parse_solution(line.replace(',40,',',nan,'))
        for changed in ({'rtk_enabled':True},{'ntrip_host':'host\r\nAuthorization: injected'},
                        {'ntrip_mountpoint':'AUTO\r\n'}, {'ntrip_password':'secret\n'}):
            with self.assertRaises(ValueError):validate(changed)


if __name__=='__main__':unittest.main()
