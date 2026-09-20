"""M2 浏览器旅程用 SSH 隧道：本机 18088 -> 服务器 localhost:8088（novel-web）。

后台常驻，直到进程被杀。用 paramiko direct-tcpip 通道 + 本地 accept 转发。
用法：``python deploy/novel/m2_tunnel.py``（前台阻塞；配合 run_in_background 用）。
"""

from __future__ import annotations

import socket
import socketserver
import sys
import threading

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from _ssh import HOST, PASSWORD, USER  # noqa: E402

import paramiko  # noqa: E402

LOCAL_PORT = 18088
REMOTE_TARGET = ("127.0.0.1", 8088)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=30,
               allow_agent=False, look_for_keys=False)
transport = client.get_transport()
print(f"[tunnel] connected {HOST}, forwarding 127.0.0.1:{LOCAL_PORT} -> {REMOTE_TARGET}", flush=True)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            chan = transport.open_channel(
                "direct-tcpip", REMOTE_TARGET, self.request.getpeername()
            )
        except Exception:
            return
        if chan is None:
            return

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(16384)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        t1 = threading.Thread(target=pump, args=(self.request, chan), daemon=True)
        t2 = threading.Thread(target=pump, args=(chan, self.request), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        chan.close()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


try:
    Server(("127.0.0.1", LOCAL_PORT), Handler).serve_forever()
except KeyboardInterrupt:
    pass
finally:
    client.close()
