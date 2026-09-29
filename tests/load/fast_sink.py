"""Minimal, fast SMTP sink for load tests: N processes share one port via SO_REUSEPORT.

Accepts and discards everything (EHLO/HELO, MAIL, RCPT, DATA, RSET, NOOP, QUIT). Local testing only.

    python tests/load/fast_sink.py --port 2525 --processes 4
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing
import os
import signal
import socket
import sys


LATENCY = 0.0  # seconds added before every reply (simulates network round-trip time to a real provider)


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"220 sink ESMTP\r\n")
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            cmd = line[:4].upper()
            if LATENCY:
                await asyncio.sleep(LATENCY)
            if cmd in (b"EHLO",):
                writer.write(b"250-sink\r\n250-8BITMIME\r\n250-SMTPUTF8\r\n250 SIZE 52428800\r\n")
            elif cmd == b"HELO":
                writer.write(b"250 sink\r\n")
            elif cmd == b"DATA":
                writer.write(b"354 end with .\r\n")
                await writer.drain()
                while True:
                    data = await reader.readuntil(b"\r\n")
                    if data == b".\r\n":
                        break
                writer.write(b"250 2.0.0 Ok: queued\r\n")
            elif cmd == b"QUIT":
                writer.write(b"221 bye\r\n")
                await writer.drain()
                break
            else:  # MAIL, RCPT, RSET, NOOP, ...
                writer.write(b"250 OK\r\n")
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def serve(port: int, latency_ms: float = 0.0) -> None:
    global LATENCY
    LATENCY = latency_ms / 1000
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass

    async def main() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("127.0.0.1", port))
        server = await asyncio.start_server(handle, sock=sock, limit=1 << 20)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--latency-ms", type=float, default=0.0, help="delay before each SMTP reply")
    args = ap.parse_args()
    procs = [multiprocessing.Process(target=serve, args=(args.port, args.latency_ms), daemon=True) for _ in range(args.processes)]
    for p in procs:
        p.start()

    def stop(*_: object) -> None:  # stop the children too, so nothing outlives the sink
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for p in procs:
        p.join()
