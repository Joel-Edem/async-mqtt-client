import socket

from async_mqtt_client import AsyncMQTTClient

try:
    import micropython
    import uasyncio
    client_name = b'mp_client'
    sub_topic = b'py_client'
except ImportError as e:
    import asyncio as uasyncio
    client_name = b'py_client'
    sub_topic = b'mp_client'


MQTT_CONFIG = {
    "client_id": client_name,
    "server": '192.168.100.228',
    "port": 1883,
    "user": b"ground_station_admin",
    "password": b"ground_station_admin_password",
    "keepalive": 0,
    "last_will": {
        "topic": "",
        "msg": "",
        "qos": 0,
        "retain": False
    }

}


def print_buf(buf):
    for i in buf:
        print("%c" % i, end='')
    print('\n')


async def connect():
    try:
        import network
    except ImportError:
        print("Could not import Network")
        return
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect('ssid', 'password')
        while not wlan.isconnected():
            await uasyncio.sleep(1)
    print('network config:', wlan.ifconfig())
    print("connected to wifi")


async def demo():
    await connect()

    recv_buf = bytearray(512)

    client = AsyncMQTTClient(MQTT_CONFIG, memoryview(recv_buf), socket, False)
    try:
        await client.connect()
    except Exception as _e:
        print(f"ERROR: could not connect to MQTT client.\n{_e}")
        return
    topic = client_name
    await client.subscribe(sub_topic)
    idx = 0
    while 1:
        await client.publish(topic, b'hello world from %s %i' % (client_name, idx))
        idx += 1
        if await client.check_msg():
            print_buf(client.topic)
            print_buf(client.msg)
            client.mark_read()
        await uasyncio.sleep(1)


if __name__ == "__main__":
    try:
        uasyncio.run(demo())
    except KeyboardInterrupt:
        print("stopped")
    finally:
        uasyncio.new_event_loop()
