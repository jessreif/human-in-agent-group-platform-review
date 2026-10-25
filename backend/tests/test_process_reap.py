"""Process cleanup and ephemeral loopback listeners. No database.

The integration process tests use the same stack: each child, log handle, socket,
and HTTP client is registered when it is acquired, then released in LIFO order.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.redaction import redact

_TERMINATE_GRACE_S = 15.0
_TAIL_BYTES = 8192
_LOOPBACK = "127.0.0.1"

# Inherit the listening fd. Ignore SIGTERM so the stack must escalate to SIGKILL.
_SIGTERM_IGNORING_CHILD = """
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write("READY\\n")
sys.stdout.flush()
while True:
    time.sleep(30)
"""

# One HTTP response: the body is the token the parent expects from THIS child.
_HTTP_CHILD = """
import socket
import sys

fd = int(sys.argv[1])
token = sys.argv[2]
sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
marker = b"\\r\\n\\r\\n"
while True:
    conn, _addr = sock.accept()
    try:
        data = b""
        while marker not in data and len(data) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        body = token.encode()
        conn.sendall(
            b"HTTP/1.1 200 OK\\r\\nContent-Type: text/plain\\r\\nContent-Length: "
            + str(len(body)).encode()
            + b"\\r\\nConnection: close\\r\\n\\r\\n"
            + body
        )
    finally:
        conn.close()
"""

_PROBE_LAUNCH = """
import json
import sys
from pathlib import Path

import tests.test_process_reap as probe

Path(sys.argv[1]).write_text(json.dumps(probe.run_listener_probe()), encoding="utf-8")
"""

BACKEND = Path(__file__).resolve().parents[1]


def socket_inode(sock: socket.socket) -> str:
    target = os.readlink(f"/proc/self/fd/{sock.fileno()}")
    prefix = "socket:["
    if not (target.startswith(prefix) and target.endswith("]")):
        raise OSError(f"fd {sock.fileno()} is not a socket")
    return target[len(prefix) : -1]


def process_holds_inode(pid: int, inode: str) -> bool:
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        names = list(fd_dir.iterdir())
    except OSError:
        return False
    needle = f"socket:[{inode}]"
    for name in names:
        try:
            target = os.readlink(name)
        except OSError:
            continue
        if target == needle:
            return True
    return False


def listen_endpoint(inode: str) -> tuple[str, int] | None:
    """`(ip, port)` if this inode is a TCP LISTEN socket, else None."""
    try:
        lines = Path("/proc/net/tcp").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 10 or parts[9] != inode or parts[3] != "0A":
            continue
        ip_hex, port_hex = parts[1].split(":")
        ip = socket.inet_ntoa(bytes.fromhex(ip_hex)[::-1])
        return ip, int(port_hex, 16)
    return None


def port_is_listening(port: int) -> bool:
    try:
        lines = Path("/proc/net/tcp").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":
            continue
        _ip_hex, port_hex = parts[1].split(":")
        if int(port_hex, 16) == port:
            return True
    return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap(proc: subprocess.Popen[bytes]) -> None:
    """terminate, wait up to 15s, SIGKILL if still alive, then wait again."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        proc.wait(timeout=_TERMINATE_GRACE_S)
        return
    try:
        proc.wait(timeout=_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            proc.wait(timeout=_TERMINATE_GRACE_S)
            return
        proc.wait(timeout=_TERMINATE_GRACE_S)


def _tail_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"(unreadable {exc.__class__.__name__})"
    text = data[-_TAIL_BYTES:].decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-100:])


class ProcessResources:
    """Register each resource at acquisition time. Cleanup is LIFO.

    A child is reaped before its log handle is closed, so a failure between two
    ``start`` calls still reaps the first child. The log tail is attached to the
    original exception and does not replace it.
    """

    def __init__(self) -> None:
        self._stack = ExitStack()
        self.procs: list[subprocess.Popen[bytes]] = []
        self.logs: list[Path] = []

    def __repr__(self) -> str:
        return f"ProcessResources(procs={len(self.procs)}, logs={len(self.logs)})"

    def __enter__(self) -> ProcessResources:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
    ) -> bool:
        cleanup_error: OSError | subprocess.SubprocessError | None = None
        try:
            self._stack.close()
        except (OSError, subprocess.SubprocessError) as caught:
            cleanup_error = caught
        if exc is not None:
            exc.add_note(self.redacted_tail())
            if cleanup_error is not None:
                exc.add_note(f"cleanup failed: {cleanup_error.__class__.__name__}")
            return False
        if cleanup_error is not None:
            raise cleanup_error
        return False

    def bind_loopback(self) -> socket.socket:
        """Bind ``127.0.0.1`` port 0 and listen. The socket is closed with the stack."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._stack.callback(sock.close)
        sock.bind((_LOOPBACK, 0))
        sock.listen(128)
        host, port = sock.getsockname()[:2]
        if host != _LOOPBACK or not isinstance(port, int) or port <= 0:
            raise OSError(f"refusing listener {host}:{port}")
        observed = listen_endpoint(socket_inode(sock))
        if observed != (_LOOPBACK, port):
            raise OSError(f"listener is {observed}, want {_LOOPBACK}:{port}")
        sock.set_inheritable(True)
        return sock

    def start(
        self,
        args: list[str],
        env: dict[str, str],
        log: Path,
        *,
        cwd: Path | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.Popen[bytes]:
        # Registered before Popen so a failed spawn still closes the handle, and
        # before the reap callback so LIFO reaps the child first.
        handle = log.open("wb")
        self._stack.callback(handle.close)
        self.logs.append(log)
        popen_kwargs: dict[str, Any] = {}
        if pass_fds:
            popen_kwargs["pass_fds"] = pass_fds
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, test harness
            args,
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
        self._stack.callback(_reap, proc)
        self.procs.append(proc)
        return proc

    def http_client(self, base_url: str) -> httpx.Client:
        client = httpx.Client(base_url=base_url, trust_env=False)
        self._stack.callback(client.close)
        return client

    def redacted_tail(self) -> str:
        if not self.logs:
            return "(no child logs)"
        parts = [f"----- {path.name} -----\n{_tail_text(path)}" for path in self.logs]
        return redact("\n".join(parts))


def run_listener_probe() -> dict[str, Any]:
    """Two loopback listeners in this process. Each request must hit its own child."""

    def _on_term(_signum: int, _frame: Any) -> None:
        raise SystemExit(1)

    signal.signal(signal.SIGTERM, _on_term)
    nonce = uuid.uuid4().hex
    tokens = (f"{nonce}-a", f"{nonce}-b")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="inv-listen-") as tmp:
        root = Path(tmp)
        with ProcessResources() as res:
            children: list[tuple[socket.socket, str, int, str, subprocess.Popen[bytes]]] = []
            for index, token in enumerate(tokens):
                sock = res.bind_loopback()
                host, port = sock.getsockname()[:2]
                proc = res.start(
                    [sys.executable, "-u", "-c", _HTTP_CHILD, str(sock.fileno()), token],
                    os.environ.copy(),
                    root / f"child-{index}.log",
                    pass_fds=(sock.fileno(),),
                )
                children.append((sock, host, port, token, proc))
            deadline = time.monotonic() + 10.0
            for sock, host, port, token, proc in children:
                inode = socket_inode(sock)
                while time.monotonic() < deadline:
                    rc = proc.poll()
                    if rc is not None:
                        raise RuntimeError(f"listener exited rc={rc}\n{res.redacted_tail()}")
                    if process_holds_inode(proc.pid, inode):
                        break
                    time.sleep(0.02)
                else:
                    raise RuntimeError(
                        f"child {proc.pid} never held 127.0.0.1:{port}\n{res.redacted_tail()}"
                    )
                response = httpx.get(f"http://127.0.0.1:{port}/id", timeout=2.0, trust_env=False)
                if response.status_code != 200 or response.text != token:
                    raise RuntimeError(
                        f"cross-talk on {host}:{port}: {response.status_code} {response.text!r} "
                        f"want {token!r}"
                    )
                results.append(
                    {
                        "host": host,
                        "port": port,
                        "token": token,
                        "body": response.text,
                        "pid": proc.pid,
                    }
                )
    for item in results:
        if pid_alive(item["pid"]):
            raise RuntimeError(f"listener pid {item['pid']} still alive")
        if port_is_listening(item["port"]):
            raise RuntimeError(f"port {item['port']} still listening")
    return {"listeners": results}


def test_reap_kills_child_that_ignores_sigterm(tmp_path: Path) -> None:
    log = tmp_path / "child.log"
    with ProcessResources() as res:
        proc = res.start(
            [sys.executable, "-u", "-c", _SIGTERM_IGNORING_CHILD],
            os.environ.copy(),
            log,
        )
        pid = proc.pid
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(
                    f"child exited before SIGTERM rc={proc.returncode}\n{res.redacted_tail()}"
                )
            if "READY" in log.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"child never ignored SIGTERM\n{res.redacted_tail()}")
        assert proc.poll() is None
        assert pid_alive(pid)
    assert proc.poll() is not None
    assert proc.returncode == -signal.SIGKILL
    assert not pid_alive(pid)


def test_failed_start_reaps_the_child_already_running(tmp_path: Path) -> None:
    """A failure after the first child is registered still reaps that child."""
    pid: int | None = None
    with pytest.raises(RuntimeError, match="second start failed"), ProcessResources() as res:
        proc = res.start(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            os.environ.copy(),
            tmp_path / "first.log",
        )
        pid = proc.pid
        raise RuntimeError("second start failed")
    assert pid is not None
    assert not pid_alive(pid)


def test_two_loopback_allocations_do_not_share_a_port() -> None:
    with ProcessResources() as res:
        first = res.bind_loopback()
        second = res.bind_loopback()
        host_a, port_a = first.getsockname()[:2]
        host_b, port_b = second.getsockname()[:2]
        assert host_a == "127.0.0.1"
        assert host_b == "127.0.0.1"
        assert port_a != port_b
        assert listen_endpoint(socket_inode(first)) == ("127.0.0.1", port_a)
        assert listen_endpoint(socket_inode(second)) == ("127.0.0.1", port_b)


def test_concurrent_probes_have_distinct_ports_and_no_crosstalk(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(BACKEND) + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else str(BACKEND)
    )
    result_paths = [tmp_path / "probe-a.json", tmp_path / "probe-b.json"]
    with ProcessResources() as res:
        procs = [
            res.start(
                [sys.executable, "-c", _PROBE_LAUNCH, str(result)],
                env,
                tmp_path / f"probe-{index}.log",
                cwd=BACKEND,
            )
            for index, result in enumerate(result_paths)
        ]
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if all(proc.poll() is not None for proc in procs):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"listener probes did not exit\n{res.redacted_tail()}")
        for proc in procs:
            if proc.returncode != 0:
                pytest.fail(f"probe exited rc={proc.returncode}\n{res.redacted_tail()}")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]
    ports = [item["port"] for payload in payloads for item in payload["listeners"]]
    tokens = [item["token"] for payload in payloads for item in payload["listeners"]]
    assert len(payloads) == 2
    assert all(len(payload["listeners"]) == 2 for payload in payloads)
    assert len(ports) == 4
    assert len(set(ports)) == 4
    assert len(set(tokens)) == 4
    for payload in payloads:
        for item in payload["listeners"]:
            assert item["host"] == "127.0.0.1"
            assert item["body"] == item["token"]
            assert not pid_alive(item["pid"])
            assert not port_is_listening(item["port"])
