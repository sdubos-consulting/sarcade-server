"""The two APRS links against fake local servers (no database)."""
import asyncio

import pytest

from sarcade.modules.adrasec.aprs import kiss, links, service


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(links, "runtime", service.AprsRuntime())
    monkeypatch.setattr(service, "runtime", links.runtime)
    yield


def test_aprs_is_login_with_filter_and_lines(monkeypatch):
    received, logins = [], []

    async def fake_handle(line, via):
        received.append((line, via))
        return 1

    monkeypatch.setattr(links, "handle_line", fake_handle)
    monkeypatch.setattr(links, "_current_filter", lambda: "b/F4JPO*")
    monkeypatch.setenv("SARCADE_APRS_CALLSIGN", "F6KRK")

    async def scenario():
        async def serve(reader, writer):
            logins.append((await reader.readline()).decode())
            writer.write(b"# aprsc 2.1\r\nF4JPO-9>APRS:!4903.50N/00201.75E>\r\n")
            await writer.drain()
            await asyncio.sleep(0.5)
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("SARCADE_APRS_IS_HOST", "127.0.0.1")
        monkeypatch.setenv("SARCADE_APRS_IS_PORT", str(port))
        task = asyncio.create_task(links.aprs_is_loop())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if received:
                break
        task.cancel()
        server.close()

    asyncio.run(scenario())
    assert logins[0] == "user F6KRK pass -1 vers SARCADE 0.1 filter b/F4JPO*\r\n", "read-only login"
    assert received == [("F4JPO-9>APRS:!4903.50N/00201.75E>", "is")], "server comments are skipped"


def test_kiss_receive_and_transmit(monkeypatch):
    received, sent = [], bytearray()

    async def fake_handle(line, via):
        received.append((line, via))
        return 1

    monkeypatch.setattr(links, "handle_line", fake_handle)
    frame = kiss.kiss_frame(kiss.encode_ui("F4JPO-9", "APRS", ["WIDE1-1"], "!4903.50N/00201.75E>"))

    async def scenario():
        async def serve(reader, writer):
            writer.write(frame)
            await writer.drain()
            sent.extend(await reader.read(1024))
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("SARCADE_KISS_HOST", "127.0.0.1")
        monkeypatch.setenv("SARCADE_KISS_PORT", str(port))
        task = asyncio.create_task(links.kiss_loop())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if received and links.runtime.radio.connected:
                break
        links.runtime.tx_queue.put_nowait(kiss.kiss_frame(kiss.encode_ui("F6KRK", "APZSAR", [], ";TEST")))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if sent:
                break
        task.cancel()
        server.close()

    asyncio.run(scenario())
    assert received == [("F4JPO-9>APRS,WIDE1-1:!4903.50N/00201.75E>", "rf")]
    frames = kiss.KissDecoder().feed(bytes(sent))
    assert kiss.ax25_to_tnc2(frames[0]) == "F6KRK>APZSAR:;TEST", "queued frame reaches the modem"


def test_login_never_n0call(monkeypatch):
    monkeypatch.delenv("SARCADE_APRS_IS_LOGIN", raising=False)
    monkeypatch.setenv("SARCADE_APRS_CALLSIGN", "")
    assert links.is_login() == ""
    monkeypatch.setenv("SARCADE_APRS_CALLSIGN", "F6KRK")
    assert links.is_login() == "F6KRK"
    monkeypatch.setenv("SARCADE_APRS_IS_LOGIN", "f4jpo-sc")
    assert links.is_login() == "F4JPO-SC"
