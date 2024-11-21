# Simple Async Mqtt Client
This is a simple implimentation of MQTT protocol for Python and MicroPython.
this implementation tries to impove performance by reducing allocations,
making it convenient for contrained environments.

## Usage
> [!WARNING]
> You should probably not use this libray unless you know what you are doing.
> This Libray is optimized for sending short messages (about 64 bytes per message). 
> as such it does not strictly conform to MQTT specification which allows messages up to 2097152 bytes.

### Creating The Client
You can create a client by passing in a config object and a buffer to hold incoming messages.
```python
import AsyncMQTTClient

recv_buf = bytearray(128)
client = AsyncMQTTClient(MQTT_CONFIG, memoryview(recv_buf))
```
The client takes a config object with the following shape 
```python
MQTT_CONFIG = {
    "client_id": b'controller',
    "server": "8.tcp.ngrok.io",
    "port": 13164,
    "user": b"mqtt_user",
    "password": b"mqtt_password",
    "keepalive": 0,
    "last_will": {
        "topic": "",
        "msg": "",
        "qos": 0,
        "retain": False
    }
}
```

### Connecting
This libray also supports using alternative sync/async socket 
implementations. This is useful on  some embeded devices without builtin socket libraries.
For example, if you implement a socket for a 2G Modoem, 
you could pass the modem as a socket like so:

```python
sock_cls = Sim800lModem
await client.connect(sock_cls=sock_cls, async_sock=True) 
```
If `sock_cls` is provided it will be used to handle all socket operations 
and so must implement methods like `read`, `write`, `set_blocking` as well as `getaddrinfo` among others.
The socket libray you provide may or may not support the `async/await` syntax. 
You MUST indicate this by setting `async_sock` To `True`.

### Subscribing
```python
await client.subscribe(b'my_topic')
```
### Publishing
```python
await client.publish(topic, b'hello world')
```
### Reading Responses
You can check if a message has been received by calling the `check_msg` method.
This method returns a `memoryview` object if a message is available or `None` if not message exists.
```python
has_message = await client.check_msg()
```
The client allocates an internal 2 byte bufffer to store meta details aboout the response. 
This is returned in the response to `check_msg` if a message is received.
This buffer contains:
1. The **length** of the `topic` at index 0
2. The **end index** of the message.

> [!INFO] The rationale behind this method is to prevent allocation of memory when reading data from the server, 
> and also prevents ceating and allocating tuples when returning.

Using this information. it is trivial to extract the topic and message from the buffer like so
```python
...

recv_buf = memoryview(bytearray(128))
meta = await client.check_msg()
if meta:
    topic =  recv_buf[:meta[0]]  # GET THE TOPIC
    message = recv_buf[meta[0]:meta[1]] # GET THE MESSAGE
    client.mark_read() # SIGNAL MESSGE HAS BEEN READ
```
alternatively you could call the following conveninece propterties to get the `topic` and   `message`

```python
if await client.check_msg():
    topic  = client.topic  # returns a slice containing only the topic
    message = client.msg   # returns a slice conatining only the message
    client.mark_read()  # mark that message has been read.
```
> [!NOTE]
> you should try to call `client.mark_read()` when done with the data to reading the same message multiple times.
> when `check_msg` is called, the client first checks if there are unread m
> essages and returns it before reading from the remote server.
> 
> This resets the counter that tracks the buffer legnth. 
> It does not cause the data in the buffer to be flushed until a new message is received

## Full Demo

```python
from async_mqtt_client import AsyncMQTTClient
import uasyncio

MQTT_CONFIG = {
    "client_id": b'controller',
    "server": "8.tcp.ngrok.io",
    "port": 13164,
    "user": b"user",
    "password": b"password",
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
    
async def demo():
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

```