# Author: Joel Ametepeh
# Date: 2024-11-21
# Description: Fast Async Mqtt Client for python / micropython.
# This implementation airms to reduce memory allocaitons and footprint.
#
#
# Copyright 2024 Joel Ametepeh <JoelAmetepeh@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

try:
    import uasyncio
    import ustruct as struct
except ImportError:
    import struct
    import asyncio as uasyncio


class AsyncMQTTClient:

    def __init__(self, config, recv_buf):
        self.client_id = config["client_id"]
        self.sock = None
        self.config = config
        self._pid = 0
        self._pid_gen = self._gen_pid()
        self._a_sock = False
        self._temp_buf = bytearray(128)
        self._recv_buf = recv_buf
        self._meta = memoryview(bytearray(2))
        self._msg_recv = False

    def next_pid(self):
        return next(self._pid_gen)

    def _gen_pid(self):
        pid = 0
        while True:
            pid = pid + 1 if pid < 65535 else 1
            self._pid = pid
            yield pid

    async def _send_str(self, s):
        if self._a_sock:
            await self.sock.a_write(struct.pack("!H", len(s)))
            await self.sock.a_write(s)
        else:
            self.sock.write(struct.pack("!H", len(s)))
            self.sock.write(s)

    async def _recv_len(self):
        n = 0
        sh = 0
        while 1:
            b = await self.sock.a_read(1)[0] if self._a_sock else self.sock.read(1)[0]
            n |= (b & 0x7F) << sh
            if not b & 0x80:
                return n
            sh += 7

    # noinspection PyUnresolvedReferences
    async def connect(self, clean_session=True, sock_cls=None, async_sock=False):
        if async_sock:
            self._a_sock = True
        if not sock_cls:
            import socket
            sock_cls = socket

        self.sock = sock_cls.socket()
        if self._a_sock:
            addr = await sock_cls.a_getaddrinfo(self.config['server'], self.config['port'])[0][-1]
        else:
            addr = sock_cls.getaddrinfo(self.config['server'], self.config['port'])[0][-1]

        await self.sock.a_connect(addr) if self._a_sock else self.sock.connect(addr)

        if self.config['port'] == 8883:
            import ssl
            self.sock = ssl.wrap_socket(self.sock)
        premsg = bytearray(b"\x10\0\0\0\0\0")
        msg = bytearray(b"\x04MQTT\x04\x02\0\0")

        sz = 10 + 2 + len(self.client_id)
        msg[6] = clean_session << 1
        if self.config['user']:
            sz += 2 + len(self.config['user']) + 2 + len(self.config['password'])
            msg[6] |= 0xC0
        if self.config['keepalive']:
            assert self.config['keepalive'] < 65536
            msg[7] |= self.config['keepalive'] >> 8
            msg[8] |= self.config['keepalive'] & 0x00FF
        if self.config["last_will"]["topic"]:
            sz += 2 + len(self.config["last_will"]["topic"]) + 2 + len(self.config["last_will"]["msg"])
            msg[6] |= 0x4 | (self.config["last_will"]["qos"] & 0x1) << 3 | (self.config["last_will"]["qos"] & 0x2) << 3
            msg[6] |= self.config["last_will"]["retain"] << 5

        i = 1
        while sz > 0x7F:
            premsg[i] = (sz & 0x7F) | 0x80
            sz >>= 7
            i += 1
        premsg[i] = sz
        # if self._a_sock:
        await self.sock.a_write(premsg[:i + 2]) if self._a_sock else self.sock.write(premsg[:i + 2])
        await self.sock.a_write(msg) if self._a_sock else self.sock.write(msg)
        await self._send_str(self.client_id)

        if self.config["last_will"]["topic"]:
            await self._send_str(self.config["last_will"]["topic"])
            await self._send_str(self.config["last_will"]["msg"])
        if self.config['user']:
            await self._send_str(self.config['user'])
            await self._send_str(self.config['password'])

        resp = await self.sock.a_read(4) if self._a_sock else self.sock.read(4)
        assert resp[0] == 0x20 and resp[1] == 0x02
        print("connected to mqtt")
        return resp[2] & 1

    async def disconnect(self):
        await self.sock.a_write(b"\xe0\0") if self._a_sock else self.sock.write(b"\xe0\0")
        await self.sock.a_close() if self._a_sock else self.sock.close()

    async def ping(self):
        await self.sock.a_write(b"\xc0\0") if self._a_sock else self.sock.write(b"\xc0\0")

    async def publish(self, topic: bytes, msg: bytes, retain=False, qos=0):
        _m = self._temp_buf
        _m[0] = 0x30 | (qos << 1 | retain)  # byte 1
        sz = 2 + len(topic) + len(msg)
        if qos > 0:
            sz += 2
        assert sz < 2097152
        _m[1] = sz  # byte 2
        idx = 2
        struct.pack_into("!H", _m, idx, len(topic))  # byte 3-4 = len(topic)
        idx += struct.calcsize("!H")
        _m[idx:len(topic)] = topic
        idx += len(topic)
        if qos > 0:
            self.next_pid()
            struct.pack_into("!H", _m, idx, self._pid)
            idx += struct.calcsize("!H")
        _m[idx:len(msg)] = msg
        idx += len(msg)

        await self.sock.a_write(memoryview(_m)[:idx]) if self._a_sock else self.sock.write(memoryview(_m)[:idx])

        if qos == 1:
            while 1:
                op = await self.wait_msg()
                if op == 0x40:
                    sz = await self.sock.a_read(1) if self._a_sock else self.sock.read(1)
                    assert sz == b"\x02"
                    rcv_pid = await self.sock.a_read(2) if self._a_sock else self.sock.read(2)
                    rcv_pid = rcv_pid[0] << 8 | rcv_pid[1]
                    if self._pid == rcv_pid:
                        return
                await uasyncio.sleep_ms(0)
        elif qos == 2:
            assert 0

    async def subscribe(self, topic, qos=0):
        pkt = bytearray(b"\x82\0\0\0")
        self.next_pid()
        struct.pack_into("!BH", pkt, 1, 2 + 2 + len(topic) + 1, self._pid)
        await self.sock.a_write(pkt) if self._a_sock else self.sock.write(pkt)
        await self._send_str(topic)
        qos = qos.to_bytes(1, "little")
        await self.sock.a_write(qos) if self._a_sock else self.sock.write(qos)

        while 1:
            op = await self.wait_msg()
            if op == 0x90:
                resp = await self.sock.a_read(4) if self._a_sock else self.sock.read(4)
                assert resp[1] == pkt[2] and resp[2] == pkt[3]
                return
            await uasyncio.sleep_ms(0)

    async def wait_msg(self):
        res = await self.sock.a_read(1) if self._a_sock else self.sock.a_read(1)
        self.sock.setblocking(True)
        if res is None:
            return None
        if res == b"":
            return None
        if res == b"\xd0":  # PING RESP
            r = await self.sock.a_read(1)[0] if self._a_sock else self.sock.read(1)[0]
            sz = r[0]
            assert sz == 0
            return None

        op = res[0]
        if op & 0xF0 != 0x30:
            return op

        sz = await self._recv_len()

        topic_len = await self.sock.a_read(2) if self._a_sock else self.sock.read(2)
        topic_len = (topic_len[0] << 8) | topic_len[1]

        await self.sock.a_readinto(self._recv_buf, topic_len) if self._a_sock else self.sock.readinto(self._recv_buf,
                                                                                                      topic_len)
        sz -= topic_len + 2
        pid = 0
        if op & 6:
            pid = await self.sock.a_read(2) if not self._a_sock else self.sock.read(2)
            pid = pid[0] << 8 | pid[1]
            sz -= 2

        memoryview(self._recv_buf)[topic_len:topic_len + sz] = await self.sock.a_read(
            sz) if self._a_sock else self.sock.read(
            sz)

        # self.cb(memoryview(self._recv_buf)[:topic_len], memoryview(self._recv_buf)[topic_len:topic_len + sz])
        self._meta[0:1] = topic_len
        self._meta[1:2] = topic_len + sz
        self._msg_recv = True
        if op & 6 == 2:
            pkt = bytearray(b"\x40\x02\0\0")
            struct.pack_into("!H", pkt, 2, pid)
            await self.sock.a_write(pkt) if self._a_sock else self.sock.write(pkt)
        return op

    async def check_msg(self) -> memoryview | None:
        """
        if we recv a msg returns status which is a buffer with the topic length and message length
        :return:
        """
        if self._msg_recv:
            return self._meta
        self.sock.setblocking(False)
        if await self.wait_msg():
            return self._meta
        return None

    def mark_read(self):
        self._msg_recv = False

    @property
    def msg(self):
        if self._msg_recv:
            return memoryview(self._recv_buf)[self._meta[0]:self._meta[1]]
        return

    @property
    def topic(self):
        if self._msg_recv:
            return memoryview(self._recv_buf)[0:self._meta[0]]

# EXAMPLE CONFIG
# MQTT_CONFIG = {
#     "client_id": b'controller',
#     "server": "8.tcp.ngrok.io",
#     "port": 13164,
#     "user": b"ground_station_admin",
#     "password": b"ground_station_admin_password",
#     "keepalive": 0,
#     "last_will": {
#         "topic": "",
#         "msg": "",
#         "qos": 0,
#         "retain": False
#     }
# }
