"""Socket-activated macOS Dashboard. Only the child imports Streamlit."""

import argparse
import asyncio
from contextlib import contextmanager, suppress
import ctypes
import os
from pathlib import Path
import signal
import socket
import sys
from tempfile import TemporaryDirectory


HOST = '127.0.0.1'
PORT = 8501
SOCKET_NAME = 'Dashboard'
IDLE_SECONDS = 600
ACTIVITY_LOCK = 'STOCKWATCH_DASHBOARD_ACTIVITY_LOCK'


def ready_file(root):
    return root / 'logs' / 'dashboard-ready'


def launchd_listener():
    lib = ctypes.CDLL(None)
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t()
    lib.launch_activate_socket.argtypes = [ctypes.c_char_p, ctypes.POINTER(type(fds)), ctypes.POINTER(type(count))]
    lib.free.argtypes = [ctypes.c_void_p]
    lib.free.restype = None
    error = lib.launch_activate_socket(SOCKET_NAME.encode(), ctypes.byref(fds), ctypes.byref(count))
    if error:
        raise OSError(error, os.strerror(error))
    try:
        if count.value != 1:
            for index in range(count.value):
                os.close(fds[index])
            raise RuntimeError('Expected one launchd Dashboard socket.')
        listener = socket.socket(fileno=fds[0])
    finally:
        lib.free(fds)
    if listener.family != socket.AF_INET or listener.getsockname() != (HOST, PORT):
        listener.close()
        raise RuntimeError('Unexpected Dashboard listener address.')
    return listener


@contextmanager
def dashboard_activity():
    """Hold the worker open for a script run, even if its tab disconnects."""
    path = os.environ.get(ACTIVITY_LOCK)
    if not path:
        yield True
        return
    import fcntl

    with open(path, 'rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


async def backend_port(child):
    """Find the actual loopback listener owned by the child using port 0."""
    probe = await asyncio.create_subprocess_exec(
        '/usr/sbin/lsof', '-nP', '-a', '-p', str(child.pid), '-iTCP', '-sTCP:LISTEN', '-Fn',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    output, _ = await probe.communicate()
    names = [line[1:] for line in output.decode().splitlines() if line.startswith('n')]
    if len(names) == 1:
        host, _, port = names[0].partition(':')
        if host == HOST and port.isdecimal():
            return int(port)
    return None


async def wait_for_backend(child):
    port = None
    try:
        async with asyncio.timeout(30):
            while child.returncode is None:
                port = port or await backend_port(child)
                if port:
                    try:
                        reader, writer = await asyncio.open_connection(HOST, port)
                        try:
                            writer.write(b'GET /_stcore/health HTTP/1.1\r\nHost: 127.0.0.1:8501\r\nConnection: close\r\n\r\n')
                            response = await asyncio.wait_for(reader.readline(), 0.25)
                            if response.split()[1:2] == [b'200']:
                                return port
                        finally:
                            writer.close()
                    except (OSError, TimeoutError):
                        pass
                await asyncio.sleep(0.2)
    except TimeoutError:
        pass
    return None


async def copy_stream(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


async def relay(reader, writer, ready):
    upstream = None
    try:
        # Chrome may preconnect without issuing a request. Such a connection
        # must not keep an otherwise closed Dashboard awake indefinitely.
        first = await asyncio.wait_for(reader.read(65536), 15)
        if not first:
            return
        port = await asyncio.shield(ready)
        if not port:
            body = b'StockWatch could not start. Please refresh in a few seconds.'
            writer.write(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nRetry-After: 5\r\n'
                         b'Content-Type: text/plain; charset=utf-8\r\nContent-Length: %d\r\n\r\n' % len(body) + body)
            await writer.drain()
            return
        backend, upstream = await asyncio.open_connection(HOST, port)
        upstream.write(first)
        outgoing = asyncio.create_task(copy_stream(reader, upstream))
        try:
            await copy_stream(backend, writer)
        finally:
            outgoing.cancel()
            await asyncio.gather(outgoing, return_exceptions=True)
    except (OSError, TimeoutError):
        pass
    finally:
        if upstream:
            upstream.close()
        writer.close()


async def wait_until_idle(server, clients, changed, idle_seconds, activity):
    import fcntl

    delay = idle_seconds
    while True:
        changed.clear()
        try:
            await asyncio.wait_for(changed.wait(), None if clients else delay)
            delay = idle_seconds
        except TimeoutError:
            if clients:
                continue
            try:
                fcntl.flock(activity, fcntl.LOCK_EX | fcntl.LOCK_NB)
                server.close()
                return
            except BlockingIOError:
                # A disconnected browser still has a script run in progress.
                delay = min(1, idle_seconds)


async def serve(listener, root, idle_seconds):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    status = ready_file(root)
    status.unlink(missing_ok=True)
    with TemporaryDirectory(prefix='stockwatch-', dir='/tmp') as folder, open(Path(folder) / 'activity', 'w+b') as activity:
        child = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'streamlit', 'run', str(root / 'app.py'),
            f'--server.address={HOST}', '--server.port=0', '--server.headless=true',
            f'--browser.serverAddress={HOST}', f'--browser.serverPort={PORT}',
            cwd=root, env={**os.environ, ACTIVITY_LOCK: activity.name},
        )
        clients = set()
        changed = asyncio.Event()
        ready = asyncio.create_task(wait_for_backend(child))
        exited = asyncio.create_task(child.wait())
        stopping = asyncio.create_task(stop.wait())
        idle = None
        server = None

        def accept(reader, writer):
            task = asyncio.create_task(relay(reader, writer, ready))
            clients.add(task)
            changed.set()

            def finished(task):
                writer.close()
                clients.discard(task)
                changed.set()

            task.add_done_callback(finished)

        try:
            server = await asyncio.start_server(accept, sock=listener)
            await asyncio.wait((ready, stopping), return_when=asyncio.FIRST_COMPLETED)
            if stopping.done():
                return
            if not ready.result():
                server.close()
                if clients:
                    await asyncio.wait(tuple(clients), timeout=2)
                raise RuntimeError('Streamlit Dashboard failed to become ready.')
            status.write_text(f'{os.getpid()} {child.pid}\n')
            idle = asyncio.create_task(wait_until_idle(server, clients, changed, idle_seconds, activity))
            await asyncio.wait((idle, exited, stopping), return_when=asyncio.FIRST_COMPLETED)
            if exited.done() and not stopping.done():
                raise RuntimeError('Streamlit Dashboard exited unexpectedly.')
        finally:
            if server:
                server.close()
            status.unlink(missing_ok=True)
            tasks = [task for task in (*clients, ready, stopping, idle) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if server:
                await server.wait_closed()
            if child.returncode is None:
                with suppress(ProcessLookupError):
                    child.terminate()
                try:
                    await asyncio.wait_for(asyncio.shield(exited), 5)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        child.kill()
                    await exited


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idle-seconds', type=int, default=IDLE_SECONDS)
    args = parser.parse_args()
    if sys.platform != 'darwin' or args.idle_seconds < 1:
        parser.error('Socket activation requires macOS and a positive idle timeout.')
    with launchd_listener() as listener:
        asyncio.run(serve(listener, Path.cwd(), args.idle_seconds))


if __name__ == '__main__':
    main()
