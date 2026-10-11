from __future__ import annotations

import asyncio
import os
import socket
import stat
import struct
import tempfile
from pathlib import Path

import pytest

from mlxctl.infrastructure.control_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ControlSocketError,
    UnixControlServer,
    read_message,
    write_message,
)


@pytest.mark.asyncio(loop_scope="function")
class TestControlProtocolV1:
    async def test_client_negotiates_and_receives_progress_then_result(
        self, async_cleanup
    ) -> None:
        async def handle(request, emit_progress):
            assert request.operation == "service.start"
            assert request.parameters == {"service": "coding"}
            await emit_progress({"phase": "starting", "completed": 1, "total": 2})
            return {"state": "ready"}

        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "mlxd.sock"
            server = UnixControlServer(socket_path, handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)

            reader, writer = await asyncio.open_unix_connection(socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)

            await write_message(
                writer,
                {
                    "type": "negotiate",
                    "protocol": PROTOCOL_NAME,
                    "supported_versions": [PROTOCOL_VERSION],
                    "request_id": "req-negotiate",
                },
            )
            assert await read_message(reader) == {
                "type": "negotiated",
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "request_id": "req-negotiate",
            }

            await write_message(
                writer,
                {
                    "type": "request",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "req-start",
                    "operation_id": "op-start",
                    "operation": "service.start",
                    "parameters": {"service": "coding"},
                },
            )

            progress = await read_message(reader)
            result = await read_message(reader)
            assert progress == {
                "type": "progress",
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "request_id": "req-start",
                "operation_id": "op-start",
                "sequence": 1,
                "progress": {"phase": "starting", "completed": 1, "total": 2},
            }
            assert result == {
                "type": "result",
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "request_id": "req-start",
                "operation_id": "op-start",
                "result": {"state": "ready"},
            }
            assert socket_path.stat().st_mode & 0o777 == 0o600
            assert socket_path.stat().st_uid == os.getuid()

    async def test_incompatible_version_returns_stable_error_before_dispatch(
        self, async_cleanup
    ) -> None:
        handled = False

        async def handle(request, emit_progress):
            nonlocal handled
            handled = True
            return {}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)
            await write_message(
                writer,
                {
                    "type": "negotiate",
                    "protocol": PROTOCOL_NAME,
                    "supported_versions": [999],
                    "request_id": "req-version",
                },
            )
            response = await read_message(reader)

            assert response["type"] == "error"
            assert response["request_id"] == "req-version"
            assert response["error"]["code"] == "unsupported_version"
            assert not handled

    async def test_oversize_frame_is_rejected_before_payload_is_read(
        self, async_cleanup
    ) -> None:
        handled = False

        async def handle(request, emit_progress):
            nonlocal handled
            handled = True
            return {}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)
            writer.write(struct.pack("!I", MAX_FRAME_BYTES + 1))
            await writer.drain()

            response = await read_message(reader)
            assert response["error"]["code"] == "frame_too_large"
            assert not handled

    async def test_malformed_json_returns_stable_error(self, async_cleanup) -> None:
        async def handle(request, emit_progress):
            return {}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)
            writer.write(struct.pack("!I", 1) + b"{")
            await writer.drain()

            response = await read_message(reader)
            assert response["error"]["code"] == "malformed_frame"

    async def test_cancel_is_dispatched_while_an_operation_is_running(
        self, async_cleanup
    ) -> None:
        operation_started = asyncio.Event()
        release_operation = asyncio.Event()
        cancelled: list[str] = []

        async def handle(request, emit_progress):
            operation_started.set()
            await release_operation.wait()
            return {"state": "stopped"}

        async def cancel(operation_id: str) -> bool:
            cancelled.append(operation_id)
            release_operation.set()
            return True

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(
                Path(directory) / "mlxd.sock", handle, cancel_handler=cancel
            )
            await server.start()
            async_cleanup.push_async_callback(server.close)
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)
            await self._negotiate(reader, writer)
            await write_message(
                writer,
                {
                    "type": "request",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "req-long",
                    "operation_id": "op-long",
                    "operation": "model.install",
                    "parameters": {},
                },
            )
            await operation_started.wait()
            await write_message(
                writer,
                {
                    "type": "cancel",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "req-cancel",
                    "operation_id": "op-long",
                },
            )

            responses = [await read_message(reader), await read_message(reader)]
            cancel_result = next(
                item for item in responses if item["request_id"] == "req-cancel"
            )
            assert cancelled == ["op-long"]
            assert cancel_result["result"] == {"cancel_requested": True}

    async def test_peer_with_different_uid_is_rejected(self, async_cleanup) -> None:
        async def handle(request, emit_progress):
            return {}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(
                Path(directory) / "mlxd.sock",
                handle,
                peer_uid_resolver=lambda peer: os.getuid() + 1,
            )
            await server.start()
            async_cleanup.push_async_callback(server.close)
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            async_cleanup.push_async_callback(self._close_writer, writer)

            response = await read_message(reader)
            assert response["error"]["code"] == "peer_not_authorized"

    async def test_start_replaces_only_a_stale_user_owned_socket(
        self, async_cleanup
    ) -> None:
        async def handle(request, emit_progress):
            return {}

        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "mlxd.sock"
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(socket_path))
            stale.close()

            server = UnixControlServer(socket_path, handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)
            assert stat_is_socket(socket_path)

    async def test_start_never_replaces_regular_file_or_symlink(self) -> None:
        async def handle(request, emit_progress):
            return {}

        with tempfile.TemporaryDirectory() as directory:
            regular_path = Path(directory) / "regular"
            regular_path.write_text("keep me")
            with pytest.raises(ControlSocketError) as regular_error:
                await UnixControlServer(regular_path, handle).start()
            assert regular_error.value.code == "unsafe_socket_path"
            assert regular_path.read_text() == "keep me"

            target = Path(directory) / "target"
            target.write_text("keep me too")
            symlink_path = Path(directory) / "link"
            symlink_path.symlink_to(target)
            with pytest.raises(ControlSocketError) as symlink_error:
                await UnixControlServer(symlink_path, handle).start()
            assert symlink_error.value.code == "unsafe_socket_path"
            assert symlink_path.is_symlink()

    async def test_close_does_not_unlink_a_replacement_socket(self) -> None:
        async def handle(request, emit_progress):
            return {}

        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "mlxd.sock"
            server = UnixControlServer(socket_path, handle)
            await server.start()
            socket_path.unlink()
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement.bind(str(socket_path))
            try:
                await server.close()
                assert stat_is_socket(socket_path)
            finally:
                replacement.close()
                socket_path.unlink(missing_ok=True)

    async def test_close_drains_an_accepted_request_before_cancelling_connections(
        self,
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handle(request, emit_progress):
            entered.set()
            await release.wait()
            return {"state": "stopped"}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            await self._negotiate(reader, writer)
            await write_message(
                writer,
                {
                    "type": "request",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": "req-stop",
                    "operation_id": "op-stop",
                    "operation": "supervisor.stop",
                    "parameters": {},
                },
            )
            await entered.wait()
            closing = asyncio.create_task(server.close())
            await asyncio.sleep(0)
            assert not closing.done()

            release.set()
            result = await read_message(reader)
            assert result["result"] == {"state": "stopped"}
            await self._close_writer(writer)
            await asyncio.wait_for(closing, timeout=1)

    async def _negotiate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await write_message(
            writer,
            {
                "type": "negotiate",
                "protocol": PROTOCOL_NAME,
                "supported_versions": [PROTOCOL_VERSION],
                "request_id": "req-negotiate",
            },
        )
        assert (await read_message(reader))["type"] == "negotiated"

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()


def stat_is_socket(path: Path) -> bool:
    return stat.S_ISSOCK(path.lstat().st_mode)
