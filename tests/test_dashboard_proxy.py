import asyncio
import os

import pytest

from stockwatch import dashboard_proxy as proxy


def test_relay_preserves_both_directions_and_http_half_close():
    async def scenario():
        headers = b'GET /_stcore/stream HTTP/1.1\r\nOrigin: http://127.0.0.1:8501\r\nCookie: x=123\r\nUpgrade: websocket\r\n\r\n'
        response = b'HTTP/1.1 101 Switching Protocols\r\n\r\n'
        payload = b'\x82\x04test'

        async def backend(reader, writer):
            assert await reader.readexactly(len(headers)) == headers
            writer.write(response)
            assert await reader.read() == payload
            writer.write(payload)
            await writer.drain()
            writer.close()

        async with await asyncio.start_server(backend, proxy.HOST, 0) as upstream:
            ready = asyncio.get_running_loop().create_future()
            ready.set_result(upstream.sockets[0].getsockname()[1])

            async def forward(reader, writer):
                await proxy.relay(reader, writer, ready)

            async with await asyncio.start_server(forward, proxy.HOST, 0) as frontend:
                reader, writer = await asyncio.open_connection(*frontend.sockets[0].getsockname())
                writer.write(headers)
                assert await reader.readexactly(len(response)) == response
                writer.write(payload)
                writer.write_eof()
                assert await reader.read() == payload
                writer.close()
                await writer.wait_closed()

    asyncio.run(asyncio.wait_for(scenario(), 3))


@pytest.mark.skipif(os.name != 'posix', reason='Dashboard uses macOS file locks')
def test_idle_waits_for_browser_and_disconnected_script(tmp_path, monkeypatch):
    class Server:
        closed = False

        def close(self):
            self.closed = True

    async def scenario():
        server, changed, clients = Server(), asyncio.Event(), {object()}
        path = tmp_path / 'activity'
        monkeypatch.setenv(proxy.ACTIVITY_LOCK, str(path))
        with path.open('w+b') as activity:
            idle = asyncio.create_task(proxy.wait_until_idle(server, clients, changed, 0.03, activity))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(idle), 0.1)
                with proxy.dashboard_activity() as running:
                    assert running
                    clients.clear()
                    changed.set()
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(idle), 0.1)
                    assert not server.closed
                await asyncio.wait_for(idle, 1)
                assert server.closed
                with proxy.dashboard_activity() as running:
                    assert not running
            finally:
                idle.cancel()
                await asyncio.gather(idle, return_exceptions=True)

    asyncio.run(scenario())
