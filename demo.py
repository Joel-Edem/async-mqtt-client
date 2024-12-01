from async_mqtt_client import AsyncMQTTClient

try:
    import uasyncio
except ImportError:
    import asyncio as uasyncio

MQTT_CONFIG = {
    "client_id": b'controller',
    "server": "2.tcp.ngrok.io",
    "port": 13514,
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
        wlan.connect('Rontelhome', 'sharon1991')
        while not wlan.isconnected():
            await uasyncio.sleep_ms(1000)
    print('network config:', wlan.ifconfig())
    print("connected to wifi")


async def demo():
    await connect()

    recv_buf = bytearray(128)
    client = AsyncMQTTClient(MQTT_CONFIG, memoryview(recv_buf))
    try:
        await client.connect(sock_cls=None, async_sock=False)
    except Exception as e:
        print(f"ERROR: could not connect to MQTT client.\n{e}")
        return
    topic = b"%s_commands" % MQTT_CONFIG['client_id']
    await client.subscribe(topic)
    try:
        while 1:
            await client.publish(topic, b'hello world')

            if await client.check_msg():
                print_buf(client.topic)
                print_buf(client.msg)
                client.mark_read()
            await uasyncio.sleep_ms(0)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    try:
        uasyncio.run(demo())
    finally:
        uasyncio.new_event_loop()
