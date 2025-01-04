try:
    import uasyncio
    import ustruct as struct
except ImportError:
    import struct
    import asyncio as uasyncio


def _is_mp() -> bool:
    try:
        import micropython
        return True
    except ImportError:
        return False


class WrappedSocket:

    def __init__(self, sock, sock_cls, async_sock: bool):
        self._sock = sock
        self._sock_cls = sock_cls
        self._async_sock = async_sock
        self._is_mp = _is_mp()

        if async_sock:
            self.write = self._a_write
        elif self._is_mp:
            self.write = self._mp_write
        else:
            self.write = self._py_write

        if self._async_sock:
            self.read = self._a_read
        elif self._is_mp:
            self.read = self._mp_read
        else:
            self.read = self._py_read

        if self._async_sock:
            self.readinto = self._a_readinto
        elif self._is_mp:
            self.readinto = self._mp_readinto
        else:
            self.readinto = self._py_readinto

        self.connect = self._connect
        self.close = self._close

    async def _mp_write(self, buf):
        return self._sock.write(buf)

    async def _a_write(self, buf):
        return await self._sock.a_write(buf)

    async def _py_write(self, buf):
        return self._sock.send(buf)

    async def _connect(self, address):
        if self._async_sock:
            return await self._sock.a_connect(address)
        else:
            return self._sock.connect(address)

    async def getaddrinfo(self, server: str, port: int):
        if self._async_sock:
            addr = await self._sock_cls.a_getaddrinfo(server, port)
        else:
            addr = self._sock_cls.getaddrinfo(server, port)

        return addr

    async def _close(self):
        if self._async_sock:
            return await self._sock.a_close()
        else:
            return self._sock.close()

    async def _mp_read(self, n_bytes: int):
        return self._sock.read(n_bytes)

    async def _py_read(self, n_bytes: int):
        self._sock.setblocking(True)
        return self._sock.recv(n_bytes)

    async def _a_read(self, n_bytes: int):
        return await self._sock.a_read(n_bytes)

    async def _mp_readinto(self, buf, sz):
        self._sock.readinto(buf, sz)

    async def _py_readinto(self, buf, sz):
        self._sock.recv_into(buf, sz)

    async def _a_readinto(self, buf, sz):
        await self._sock.a_readinto(buf, sz)


class AsyncMQTTClient:

    def __init__(self, config, recv_buf, sock_cls, async_sock: bool):
        self.client_id = config["client_id"]

        self.sock = sock_cls.socket()
        self._wrapped = WrappedSocket(self.sock, sock_cls, async_sock)

        self.config = config
        self._pid = 0
        self._pid_gen = self._gen_pid()
        self._a_sock = async_sock
        self._recv_buf = memoryview(recv_buf)
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
        await self._wrapped.write(struct.pack("!H", len(s)))
        await self._wrapped.write(s)

    async def _recv_len(self):
        n = 0
        sh = 0
        while 1:
            b = (await self._wrapped.read(1))[0]
            n |= (b & 0x7F) << sh
            if not b & 0x80:
                return n
            sh += 7

    # noinspection PyUnresolvedReferences
    async def connect(self, clean_session=True, ):
        addr = (await self._wrapped.getaddrinfo(self.config['server'], self.config['port']))[0][-1]

        await self._wrapped.connect(addr)

        if self.config['port'] == 8883:
            import ssl
            self.sock = ssl.wrap_socket(self.sock)
            self._wrapped.sock = self.sock
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

        await self._wrapped.write(premsg[:i + 2])
        await self._wrapped.write(msg)
        await self._send_str(self.client_id)

        if self.config["last_will"]["topic"]:
            await self._send_str(self.config["last_will"]["topic"])
            await self._send_str(self.config["last_will"]["msg"])
        if self.config['user']:
            await self._send_str(self.config['user'])
            await self._send_str(self.config['password'])

        resp = await self._wrapped.read(4)

        assert resp[0] == 0x20 and resp[1] == 0x02
        print("connected to mqtt")
        return (resp[2] & 1) == 0

    async def disconnect(self):
        await self._wrapped.write(b"\xe0\0")
        await self._wrapped.close()

    async def ping(self):
        await self._wrapped.write(b"\xc0\0")

    async def publish(self, topic: bytes, msg: bytes, retain=False, qos=0, raise_exception=True):
        try:
            await self._publish(topic, msg, retain, qos, )
            return True
        except Exception as e:
            print(f"MQTT CLIENT: Exception {e} occurred while publishing {msg} to {topic}")
            if raise_exception:
                raise e
            return False

    async def _publish(self, topic: bytes, msg: bytes, retain=False, qos=0):
        buf = self._recv_buf
        idx = 0
        buf[idx] = 0x30 | (qos << 1 | retain)  # byte 1
        idx += 1
        sz = 2 + len(topic) + len(msg)
        if qos > 0:
            sz += 2
        assert sz < 2097152
        buf[idx] = sz  # byte 2
        idx += 1

        struct.pack_into("!H", buf, idx, len(topic))  # byte 3-4 = len(topic)
        idx += struct.calcsize("!H")
        buf[idx:idx + len(topic)] = topic
        idx += len(topic)
        if qos > 0:
            self.next_pid()
            struct.pack_into("!H", buf, idx, self._pid)
            idx += struct.calcsize("!H")
        buf[idx:idx + len(msg)] = msg
        idx += len(msg)
        await self._wrapped.write(buf[:idx])

        if qos == 1:
            while 1:
                op = await self.wait_msg()
                if op == 0x40:
                    sz = await self._wrapped.read(1)
                    assert sz == b"\x02"
                    rcv_pid = await self._wrapped.read(2)
                    rcv_pid = rcv_pid[0] << 8 | rcv_pid[1]
                    if self._pid == rcv_pid:
                        return
                await uasyncio.sleep(0)
        elif qos == 2:
            assert 0

    async def subscribe(self, topic, qos=0):
        pkt = bytearray(b"\x82\0\0\0")
        self.next_pid()
        struct.pack_into("!BH", pkt, 1, 2 + 2 + len(topic) + 1, self._pid)
        await self._wrapped.write(pkt)
        await self._send_str(topic)
        qos = qos.to_bytes(1, "little")
        await self._wrapped.write(qos)

        while 1:
            op = await self.wait_msg()
            if op == 0x90:
                resp = await self._wrapped.read(4)
                assert resp[1] == pkt[2] and resp[2] == pkt[3]
                return
            await uasyncio.sleep(0)

    async def wait_msg(self):
        res = await self._wrapped.read(1)
        self.sock.setblocking(True)
        if res is None:
            return None
        if res == b"":
            return None
        if res == b"\xd0":  # PING RESP
            sz = (await self._wrapped.read(1))[0]
            assert sz == 0
            return None

        op = res[0]
        if op & 0xF0 != 0x30:
            return op

        sz = await self._recv_len()

        topic_len = await self._wrapped.read(2)
        topic_len = (topic_len[0] << 8) | topic_len[1]

        await self._wrapped.readinto(self._recv_buf, topic_len)

        sz -= topic_len + 2
        pid = 0
        if op & 6:
            pid = await self._wrapped.read(2)
            pid = pid[0] << 8 | pid[1]
            sz -= 2
        await self._wrapped.readinto(self._recv_buf[topic_len:topic_len + sz], sz)

        self._meta[0] = topic_len
        self._meta[1] = topic_len + sz
        self._msg_recv = True
        if op & 6 == 2:
            pkt = bytearray(b"\x40\x02\0\0")
            struct.pack_into("!H", pkt, 2, pid)
            await self._wrapped.write(pkt)
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
            return self._recv_buf[self._meta[0]:self._meta[1]]

    @property
    def topic(self):
        if self._msg_recv:
            return self._recv_buf[0:self._meta[0]]

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
