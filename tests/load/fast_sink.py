"""Minimal, fast SMTP sink for load tests: N processes share one port via SO_REUSEPORT.

Accepts and discards everything (EHLO/HELO, MAIL, RCPT, DATA, BDAT, RSET, NOOP, QUIT) and advertises
PIPELINING + CHUNKING unless --no-pipelining. Local testing only.

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


LATENCY = 0.0  # seconds per round trip (simulates the network distance to a real provider)
PIPELINING = True  # advertise PIPELINING + CHUNKING (RFC 2920 / RFC 3030), like large providers do


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Replies to everything that arrived in one read are sent together after one LATENCY delay, which
    models a round trip: pipelined commands cost one delay, lock-step commands one delay each."""
    ext = b"250-sink\r\n" + (b"250-PIPELINING\r\n250-CHUNKING\r\n" if PIPELINING else b"")
    ext += b"250-8BITMIME\r\n250-SMTPUTF8\r\n250 SIZE 52428800\r\n"
    pending = [b"220 sink ESMTP\r\n"]
    buf = b""
    in_data = False
    bdat_left = 0
    try:
        while True:
            if pending:
                if LATENCY:
                    await asyncio.sleep(LATENCY)
                writer.write(b"".join(pending))
                await writer.drain()
                pending = []
            chunk = await reader.read(1 << 16)
            if not chunk:
                break
            buf += chunk
            while True:
                if bdat_left:
                    take = min(bdat_left, len(buf))
                    buf = buf[take:]
                    bdat_left -= take
                    if bdat_left:
                        break
                    pending.append(b"250 2.0.0 Ok: queued\r\n")
                    continue
                if in_data:
                    end = buf.find(b"\r\n.\r\n")
                    if end == -1:
                        buf = buf[-4:]  # keep a possible partial terminator
                        break
                    buf = buf[end + 5:]
                    in_data = False
                    pending.append(b"250 2.0.0 Ok: queued\r\n")
                    continue
                if b"\r\n" not in buf:
                    break
                line, buf = buf.split(b"\r\n", 1)
                cmd = line[:4].upper()
                if cmd == b"EHLO":
                    pending.append(ext)
                elif cmd == b"HELO":
                    pending.append(b"250 sink\r\n")
                elif cmd == b"DATA":
                    pending.append(b"354 end with .\r\n")
                    in_data = True
                    buf = b"\r\n" + buf  # lets an empty body's lone "." match the terminator
                elif cmd == b"BDAT":
                    bdat_left = int(line.split()[1])
                    if not bdat_left:
                        pending.append(b"250 2.0.0 Ok: queued\r\n")
                elif cmd == b"QUIT":
                    writer.write(b"221 bye\r\n")
                    await writer.drain()
                    return
                else:  # MAIL, RCPT, RSET, NOOP, ...
                    pending.append(b"250 OK\r\n")
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        pass
    finally:
        writer.close()


def serve(port: int, latency_ms: float = 0.0, pipelining: bool = True) -> None:
    global LATENCY, PIPELINING
    LATENCY = latency_ms / 1000
    PIPELINING = pipelining
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
    ap.add_argument("--latency-ms", type=float, default=0.0, help="simulated network round trip per reply batch")
    ap.add_argument("--no-pipelining", action="store_true", help="do not advertise PIPELINING/CHUNKING")
    args = ap.parse_args()
    procs = [multiprocessing.Process(target=serve, args=(args.port, args.latency_ms, not args.no_pipelining), daemon=True)
             for _ in range(args.processes)]
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
