"""Byte pipe in front of the disposable UI.

A connection to the control port closes the sockets that are open at that
moment, so the browser's event stream errors and reconnects. Later connections
are forwarded again. Nothing here is logged.

    cut_proxy.py LISTEN_PORT TARGET_PORT CONTROL_PORT_FILE
"""

from __future__ import annotations

import select
import socket
import struct
import sys
import threading
from pathlib import Path

LISTEN = int(sys.argv[1])
TARGET = int(sys.argv[2])
CONTROL_PATH = Path(sys.argv[3])
_OPEN: set[socket.socket] = set()
_LOCK = threading.Lock()


def _reset(sock: socket.socket) -> None:
    # shutdown wakes the thread blocked in recv and FINs the peer. close()
    # alone does not, while that recv is still in progress, so the browser
    # would keep the event stream open.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _drop(_signum: int, _frame: object) -> None:
    with _LOCK:
        current = list(_OPEN)
    # One line, no payloads: how many sockets this cut actually held.
    try:
        CONTROL_PATH.with_name(".cut-status").write_text(
            f"open={len(current)}\n", encoding="ascii"
        )
    except OSError:
        pass
    for sock in current:
        _reset(sock)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _handle(client: socket.socket) -> None:
    try:
        upstream = socket.create_connection(("127.0.0.1", TARGET))
    except OSError:
        client.close()
        return
    with _LOCK:
        _OPEN.add(client)
        _OPEN.add(upstream)
    left = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
    right = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
    left.start()
    right.start()
    left.join()
    right.join()
    with _LOCK:
        _OPEN.discard(client)
        _OPEN.discard(upstream)


def _serve(server: socket.socket, control: socket.socket) -> None:
    while True:
        readable, _, _ = select.select([server, control], [], [])
        if control in readable:
            try:
                knob, _addr = control.accept()
            except OSError:
                knob = None
            if knob is not None:
                knob.close()
            _drop(0, None)
        if server in readable:
            try:
                client, _addr = server.accept()
            except OSError:
                continue
            threading.Thread(target=_handle, args=(client,), daemon=True).start()


def main() -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", LISTEN))
    server.listen(64)
    control = socket.socket()
    control.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    control.bind(("127.0.0.1", 0))
    control.listen(8)
    CONTROL_PATH.write_text(f"{control.getsockname()[1]}\n", encoding="ascii")
    _serve(server, control)


if __name__ == "__main__":
    main()
