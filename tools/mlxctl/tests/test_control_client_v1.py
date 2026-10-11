from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from mlxctl.infrastructure.control_client import (
    AsyncUnixControlClient,
    ControlClientError,
    ControlConnectionError,
    ControlProtocolFailure,
    RemoteControlError,
    SupervisorUnavailableError,
    UnixControlClient,
)
from mlxctl.infrastructure.control_protocol import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ControlProtocolError,
    UnixControlServer,
    read_message,
    write_message,
)


@pytest.mark.asyncio(loop_scope="function")
class TestAsyncUnixControlClient:
    async def test_execute_negotiates_and_returns_correlated_ordered_progress(
        self, async_cleanup
    ) -> None:
        async def handle(request, emit_progress):
            assert request.request_id == "request-42"
            assert request.operation_id == "operation-42"
            assert request.operation == "service.start"
            assert request.parameters == {"service": "coding"}
            await emit_progress({"phase": "allocating", "completed": 1, "total": 2})
            await emit_progress({"phase": "ready", "completed": 2, "total": 2})
            return {"state": "ready", "service": "coding"}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)

            response = await AsyncUnixControlClient(server.socket_path).execute(
                "service.start",
                {"service": "coding"},
                request_id="request-42",
                operation_id="operation-42",
            )

        assert response.request_id == "request-42"
        assert response.operation_id == "operation-42"
        assert response.progress == (
            {"phase": "allocating", "completed": 1, "total": 2},
            {"phase": "ready", "completed": 2, "total": 2},
        )
        assert response.result == {"state": "ready", "service": "coding"}

    async def test_synchronous_facade_uses_the_same_protocol_contract(
        self, async_cleanup
    ) -> None:
        async def handle(request, emit_progress):
            return {"supervisor": "running"}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)

            response = await asyncio.to_thread(
                UnixControlClient(server.socket_path).execute,
                "status",
                request_id="request-sync",
                operation_id="operation-sync",
            )

        assert response.request_id == "request-sync"
        assert response.operation_id == "operation-sync"
        assert response.result == {"supervisor": "running"}

    async def test_missing_supervisor_socket_has_a_distinct_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = AsyncUnixControlClient(Path(directory) / "missing.sock")

            with pytest.raises(SupervisorUnavailableError) as raised:
                await client.execute("status")

        assert raised.value.code == "supervisor_unavailable"
        assert "not running" in raised.value.message

    async def test_invalid_identifiers_fail_before_connecting(
        self, subtests: pytest.Subtests
    ) -> None:
        client = AsyncUnixControlClient("/a/socket/that/does/not/exist")

        for call in (
            client.execute(""),
            client.execute("status", request_id=""),
            client.execute("status", operation_id=""),
            client.cancel(""),
        ):
            with subtests.test(call=call):
                with pytest.raises(ControlClientError) as raised:
                    await call
                assert raised.value.code == "invalid_request"

    async def test_supervisor_operation_error_keeps_its_stable_code(
        self, async_cleanup
    ) -> None:
        async def handle(request, emit_progress):
            raise ControlProtocolError(
                "service_unknown", "No Inference Service named 'missing' exists."
            )

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)

            with pytest.raises(RemoteControlError) as raised:
                await AsyncUnixControlClient(server.socket_path).execute(
                    "service.start", {"service": "missing"}
                )

        assert raised.value.code == "service_unknown"
        assert raised.value.message == "No Inference Service named 'missing' exists."

    async def test_whole_exchange_has_a_bounded_timeout(self, async_cleanup) -> None:
        release = asyncio.Event()

        async def stalled(reader, writer):
            await release.wait()
            writer.close()

        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "mlxd.sock"
            server = await asyncio.start_unix_server(stalled, path=socket_path)
            async_cleanup.push_async_callback(self._close_server, server)

            try:
                with pytest.raises(ControlConnectionError) as raised:
                    await AsyncUnixControlClient(
                        socket_path, timeout_seconds=0.01
                    ).execute("status")
            finally:
                release.set()

        assert raised.value.code == "control_timeout"

    async def test_out_of_order_progress_is_a_protocol_failure(
        self, async_cleanup
    ) -> None:
        async def out_of_order(reader, writer):
            negotiation = await read_message(reader)
            await write_message(
                writer,
                {
                    "type": "negotiated",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": negotiation["request_id"],
                },
            )
            request = await read_message(reader)
            await write_message(
                writer,
                {
                    "type": "progress",
                    "protocol": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                    "request_id": request["request_id"],
                    "operation_id": request["operation_id"],
                    "sequence": 2,
                    "progress": {"phase": "skipped"},
                },
            )
            writer.close()
            await writer.wait_closed()

        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "mlxd.sock"
            server = await asyncio.start_unix_server(out_of_order, path=socket_path)
            async_cleanup.push_async_callback(self._close_server, server)

            with pytest.raises(ControlProtocolFailure) as raised:
                await AsyncUnixControlClient(socket_path).execute("status")

        assert raised.value.code == "invalid_progress"

    async def test_outbound_and_inbound_frames_use_the_configured_bound(
        self, async_cleanup
    ) -> None:
        handled = False

        async def handle(request, emit_progress):
            nonlocal handled
            handled = True
            return {"payload": "x" * 1_000}

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(Path(directory) / "mlxd.sock", handle)
            await server.start()
            async_cleanup.push_async_callback(server.close)
            client = AsyncUnixControlClient(server.socket_path, max_frame_bytes=256)

            with pytest.raises(ControlProtocolFailure) as outbound:
                await client.execute("service.start", {"payload": "x" * 1_000})
            assert not handled
            assert outbound.value.code == "frame_too_large"

            with pytest.raises(ControlProtocolFailure) as inbound:
                await client.execute("status")
            assert handled
            assert inbound.value.code == "frame_too_large"

    async def test_cancel_uses_the_protocol_cancel_envelope(
        self, async_cleanup
    ) -> None:
        cancelled: list[str] = []

        async def handle(request, emit_progress):
            return {}

        async def cancel(operation_id):
            cancelled.append(operation_id)
            return True

        with tempfile.TemporaryDirectory() as directory:
            server = UnixControlServer(
                Path(directory) / "mlxd.sock", handle, cancel_handler=cancel
            )
            await server.start()
            async_cleanup.push_async_callback(server.close)

            response = await AsyncUnixControlClient(server.socket_path).cancel(
                "operation-long", request_id="request-cancel"
            )

        assert cancelled == ["operation-long"]
        assert response.request_id == "request-cancel"
        assert response.operation_id == "operation-long"
        assert response.result == {"cancel_requested": True}

    @staticmethod
    async def _close_server(server: asyncio.AbstractServer) -> None:
        server.close()
        await server.wait_closed()
