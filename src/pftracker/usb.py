"""CH340 userspace receiver using FreeBSD's base-system libusb (no PyUSB).

Register meanings: FreeBSD sys/dev/usb/serial/uchcom.c; CH340 clock is 48 MHz.
Only the USB UART is configured; no configuration commands are sent to the GNSS.
"""
import ctypes as C
import ctypes.util
import time


def baud_register(baud):
    # Choose the closest achievable rate, preferring the lower base clock on ties.
    choices = []
    for prescale in range(4):
        for fast in range(2):
            clock = 48_000_000 / (2 ** (12 - 3 * prescale - fast))
            div = round(clock / baud)
            if (9 if fast else 2) <= div <= 256:
                choices.append((abs(clock / div - baud), fast, prescale, div))
    if not choices:
        raise ValueError('Unsupported baud rate')
    _, fast, prescale, div = min(choices)
    return ((256 - div) << 8) | (fast << 2) | prescale


class CH340:
    def __init__(self, baud=115200, selector='', vid=0x1a86, pid=0x7523):
        self.lib = C.CDLL(ctypes.util.find_library('usb') or '/usr/lib/libusb.so.3')
        self.ctx, self.handle = C.c_void_p(), C.c_void_p()
        self.claimed = False
        signatures = {
            'init': (C.c_int, [C.POINTER(C.c_void_p)]),
            'exit': (None, [C.c_void_p]),
            'get_device_list': (C.c_ssize_t, [C.c_void_p, C.POINTER(C.POINTER(C.c_void_p))]),
            'free_device_list': (None, [C.POINTER(C.c_void_p), C.c_int]),
            'get_device_descriptor': (C.c_int, [C.c_void_p, C.c_void_p]),
            'get_bus_number': (C.c_uint8, [C.c_void_p]),
            'get_device_address': (C.c_uint8, [C.c_void_p]),
            'open': (C.c_int, [C.c_void_p, C.POINTER(C.c_void_p)]),
            'close': (None, [C.c_void_p]),
            'claim_interface': (C.c_int, [C.c_void_p, C.c_int]),
            'release_interface': (C.c_int, [C.c_void_p, C.c_int]),
            'control_transfer': (C.c_int, [C.c_void_p, C.c_uint8, C.c_uint8, C.c_uint16, C.c_uint16, C.c_void_p, C.c_uint16, C.c_uint]),
            'bulk_transfer': (C.c_int, [C.c_void_p, C.c_ubyte, C.c_void_p, C.c_int, C.POINTER(C.c_int), C.c_uint]),
        }
        for name, (restype, args) in signatures.items():
            f = getattr(self.lib, 'libusb_' + name)
            f.restype, f.argtypes = restype, args
        self.check(self.lib.libusb_init(C.byref(self.ctx)))
        devices = C.POINTER(C.c_void_p)()
        try:
            n = self.lib.libusb_get_device_list(self.ctx, C.byref(devices))
            self.check(n)
            matches = []
            for i in range(n):
                desc = C.create_string_buffer(18)
                self.check(self.lib.libusb_get_device_descriptor(devices[i], desc))
                b = desc.raw
                if int.from_bytes(b[8:10], 'little') != vid or int.from_bytes(b[10:12], 'little') != pid:
                    continue
                name = 'ugen%d.%d' % (self.lib.libusb_get_bus_number(devices[i]), self.lib.libusb_get_device_address(devices[i]))
                if not selector or selector == name:
                    matches.append((devices[i], name))
            if len(matches) != 1:
                raise OSError('Expected one matching CH340, found %d; check USB selector' % len(matches))
            self.check(self.lib.libusb_open(matches[0][0], C.byref(self.handle)))
            self.name = matches[0][1]
            self.check(self.lib.libusb_claim_interface(self.handle, 0))
            self.claimed = True
            # Read descriptors through endpoint zero; select a bulk IN endpoint.
            cfg = self.control(0x80, 6, 0x0200, 0, 256)
            self.endpoint = None
            offset = 0
            while offset + 2 <= len(cfg) and cfg[offset] >= 2:
                size, kind = cfg[offset:offset+2]
                if kind == 5 and size >= 7 and cfg[offset+2] & 0x80 and cfg[offset+3] & 3 == 2:
                    self.endpoint = cfg[offset+2]
                    self.packet_size = int.from_bytes(cfg[offset+4:offset+6], 'little')
                    break
                offset += size
            if self.endpoint is None:
                raise OSError('No bulk IN endpoint')
            version = self.control(0xc0, 0x5f, 0, 0, 2)[0]
            if version < 0x30:
                raise OSError('CH340 version 0x%02x is not supported by this receiver' % version)
            self.control(0x40, 0xa1, 0, 0)
            self.control(0x40, 0x9a, 0x1312, baud_register(baud) | 0x80)
            self.control(0x40, 0x9a, 0x2518, 0x00c3)  # RX/TX, 8N1
            self.control(0x40, 0xa4, 0xff9f, 0)  # assert DTR/RTS
        except BaseException:
            self.close()
            raise
        finally:
            if devices:
                self.lib.libusb_free_device_list(devices, 1)

    @staticmethod
    def check(result):
        if result < 0:
            raise OSError('libusb error %d' % result)
        return result

    def control(self, kind, request, value, index, size=0):
        buf = C.create_string_buffer(max(size, 1))
        n = self.check(self.lib.libusb_control_transfer(self.handle, kind, request, value, index, buf, size, 1000))
        return buf.raw[:n]

    def read(self):
        buf, n = C.create_string_buffer(self.packet_size), C.c_int()
        rc = self.lib.libusb_bulk_transfer(self.handle, self.endpoint, buf, len(buf), C.byref(n), 500)
        if rc != -7:  # timeout can still contain a partial transfer
            self.check(rc)
        return buf.raw[:n.value]

    def close(self):
        if self.handle:
            if self.claimed:
                self.lib.libusb_release_interface(self.handle, 0)
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = C.c_void_p()


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--seconds', type=int, default=10)
    a = p.parse_args()
    usb = CH340(a.baud)
    try:
        print('Reading', usb.name, 'endpoint', hex(usb.endpoint), flush=True)
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            print(usb.read().decode('ascii', errors='replace'), end='', flush=True)
    finally:
        usb.close()
