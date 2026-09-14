"""Offline stream-correctness and cleanup checks for the TCP/VSOCK relay."""

from collections import deque
import errno
import socket
import threading
import unittest
from unittest import mock

import traffic_forwarder as forwarder


class ScriptedSocket:
    """Expose deterministic short writes and timeouts without real traffic."""

    def __init__(self, reads=(), sends=(), shutdown_error=None):
        self.reads = deque(reads)
        self.sends = deque(sends)
        self.sent = bytearray()
        self.send_arguments = []
        self.shutdown_calls = []
        self.shutdown_error = shutdown_error
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        result = self.reads.popleft()
        if isinstance(result, BaseException):
            raise result
        return result

    def send(self, data):
        self.send_arguments.append(bytes(data))
        result = self.sends.popleft() if self.sends else len(data)
        if isinstance(result, BaseException):
            raise result
        count = min(result, len(data))
        self.sent.extend(data[:count])
        return count

    def shutdown(self, how):
        self.shutdown_calls.append(how)
        if self.shutdown_error is not None:
            raise self.shutdown_error


class TimeoutObservedSocket:
    """Use real socket I/O and signal when downstream backpressure persists."""

    def __init__(self, sock):
        self.sock = sock
        self.write_timed_out = threading.Event()

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def send(self, data):
        try:
            return self.sock.send(data)
        except socket.timeout:
            self.write_timed_out.set()
            raise


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        forwarder.shutdown_flag.clear()
        self.sockets = []
        self.workers = []
        self.worker_errors = []

    def tearDown(self):
        forwarder.shutdown_flag.set()
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in self.workers:
            worker.join(2)
        for sock in self.sockets:
            sock.close()
        forwarder.shutdown_flag.clear()
        self.assertFalse(any(worker.is_alive() for worker in self.workers))
        self.assertEqual(self.worker_errors, [])

    def socket_pair(self):
        pair = socket.socketpair()
        for sock in pair:
            sock.settimeout(2)
            self.sockets.append(sock)
        return pair

    def tcp_pair(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client = socket.create_connection(listener.getsockname(), timeout=2)
            try:
                accepted, _ = listener.accept()
            except BaseException:
                client.close()
                raise
        for sock in (client, accepted):
            sock.settimeout(2)
            self.sockets.append(sock)
        return client, accepted

    def start_worker(self, target, *args):
        def run():
            try:
                target(*args)
            except BaseException as error:
                self.worker_errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        self.workers.append(worker)
        worker.start()
        return worker

    def assert_worker_finished(self, worker):
        worker.join(3)
        self.assertFalse(worker.is_alive(), "forwarding worker did not stop")
        self.assertEqual(self.worker_errors, [])

    def read_to_eof(self, sock):
        received = bytearray()
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                return bytes(received)
            received.extend(chunk)

    def test_short_writes_and_write_timeout_preserve_exact_remaining_bytes(self):
        source = ScriptedSocket(reads=[b"abcdef", b"next", b""])
        destination = ScriptedSocket(sends=[2, socket.timeout(), 1, 3])
        stopped = threading.Event()

        forwarder.forward(source, destination, "synthetic", "client->server", stopped)

        self.assertEqual(destination.sent, b"abcdefnext")
        self.assertEqual(
            destination.send_arguments,
            [b"abcdef", b"cdef", b"cdef", b"def", b"next"],
        )
        self.assertFalse(stopped.is_set())
        self.assertEqual(destination.shutdown_calls, [socket.SHUT_WR])
        self.assertEqual(source.shutdown_calls, [])

    def test_read_timeout_keeps_waiting_for_next_bytes(self):
        source = ScriptedSocket(reads=[socket.timeout(), b"after idle", b""])
        destination = ScriptedSocket()

        forwarder.forward(source, destination, "synthetic", "client->server")

        self.assertEqual(destination.sent, b"after idle")

    def test_zero_length_write_aborts_both_sides(self):
        source = ScriptedSocket(reads=[b"pending"])
        destination = ScriptedSocket(sends=[0])
        stopped = threading.Event()

        forwarder.forward(source, destination, "synthetic", "client->server", stopped)

        self.assertTrue(stopped.is_set())
        self.assertIn(socket.SHUT_RDWR, source.shutdown_calls)
        self.assertIn(socket.SHUT_RDWR, destination.shutdown_calls)

    def test_peer_disconnect_is_identified_without_logging_exception_text(self):
        secret = "synthetic-credential-canary"
        for error in (BrokenPipeError(errno.EPIPE, secret),
                      ConnectionResetError(errno.ECONNRESET, secret)):
            with self.subTest(errno=error.errno):
                source = ScriptedSocket(reads=[b"pending"])
                destination = ScriptedSocket(sends=[error])
                stopped = threading.Event()

                with self.assertLogs(level="INFO") as captured:
                    forwarder.forward(
                        source, destination, "synthetic", "client->server", stopped
                    )

                self.assertTrue(stopped.is_set())
                self.assertIn("Peer disconnected", "\n".join(captured.output))
                self.assertNotIn(secret, "\n".join(captured.output))
                self.assertFalse(any(record.levelname == "ERROR" for record in captured.records))

    def test_unexpected_errors_remain_errors_without_exception_text(self):
        secret = "synthetic-credential-canary"
        for error in (OSError(errno.EIO, secret), RuntimeError(secret)):
            with self.subTest(error_type=type(error).__name__):
                source = ScriptedSocket(reads=[error])
                destination = ScriptedSocket()
                stopped = threading.Event()

                with self.assertLogs(level="ERROR") as captured:
                    forwarder.forward(
                        source, destination, "synthetic", "client->server", stopped
                    )

                self.assertTrue(stopped.is_set())
                self.assertNotIn(secret, "\n".join(captured.output))
                self.assertTrue(any(record.levelname == "ERROR" for record in captured.records))

    def test_shutdown_error_on_one_socket_does_not_skip_other_socket(self):
        source = ScriptedSocket(
            reads=[OSError(errno.EIO, "synthetic I/O failure")],
            shutdown_error=OSError(errno.ENOTCONN, "already disconnected"),
        )
        destination = ScriptedSocket()

        forwarder.forward(source, destination, "synthetic", "client->server")

        self.assertIn(socket.SHUT_RDWR, source.shutdown_calls)
        self.assertIn(socket.SHUT_RDWR, destination.shutdown_calls)

    def test_real_backpressure_preserves_payload_after_write_timeout(self):
        producer, source = self.socket_pair()
        outgoing, consumer = self.socket_pair()
        outgoing.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        outgoing.settimeout(0.1)
        destination = TimeoutObservedSocket(outgoing)
        payload = bytes(range(256)) * 256

        def produce():
            producer.sendall(payload)
            producer.shutdown(socket.SHUT_WR)

        writer = self.start_worker(produce)
        relay = self.start_worker(
            forwarder.forward, source, destination, "synthetic", "client->server"
        )
        self.assertTrue(destination.write_timed_out.wait(3), "no downstream timeout observed")

        self.assertEqual(self.read_to_eof(consumer), payload)
        self.assert_worker_finished(writer)
        self.assert_worker_finished(relay)

    def test_request_half_close_allows_complete_reverse_response(self):
        client, incoming = self.tcp_pair()
        outgoing, service = self.tcp_pair()
        stopped = threading.Event()
        outbound = self.start_worker(
            forwarder.forward, incoming, outgoing, "synthetic", "client->server", stopped
        )
        inbound = self.start_worker(
            forwarder.forward, outgoing, incoming, "synthetic", "server->client", stopped
        )

        client.sendall(b"synthetic request")
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(self.read_to_eof(service), b"synthetic request")
        self.assert_worker_finished(outbound)
        self.assertTrue(inbound.is_alive(), "request EOF prematurely stopped the response")
        self.assertFalse(stopped.is_set())

        service.sendall(b"synthetic response after request EOF")
        service.shutdown(socket.SHUT_WR)
        self.assertEqual(self.read_to_eof(client), b"synthetic response after request EOF")
        self.assert_worker_finished(inbound)
        self.assertFalse(stopped.is_set())

    def test_closed_consumer_stops_other_direction_without_waiting_for_peer_eof(self):
        client, incoming = self.socket_pair()
        outgoing, service = self.socket_pair()
        stopped = threading.Event()
        # SHUT_RD on a peer does not reliably produce EPIPE on every platform.
        # Inject that single write result while the opposite worker uses a real,
        # still-open socket and is waiting for data from the service.
        destination = mock.Mock(wraps=outgoing)
        destination.send.side_effect = BrokenPipeError(errno.EPIPE, "synthetic closed reader")
        outbound = self.start_worker(
            forwarder.forward, incoming, destination, "synthetic", "client->server", stopped
        )
        inbound = self.start_worker(
            forwarder.forward, outgoing, incoming, "synthetic", "server->client", stopped
        )

        client.sendall(b"synthetic pending request")

        self.assertTrue(stopped.wait(3), "closed destination did not cancel the pair")
        self.assert_worker_finished(outbound)
        self.assert_worker_finished(inbound)

    def test_global_shutdown_stops_a_backpressured_writer(self):
        producer, source = self.socket_pair()
        outgoing, consumer = self.socket_pair()
        outgoing.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        outgoing.setblocking(False)
        while True:
            try:
                outgoing.send(b"x" * 1024)
            except BlockingIOError:
                break
        outgoing.settimeout(0.1)
        destination = TimeoutObservedSocket(outgoing)
        producer.sendall(b"synthetic pending bytes")
        relay = self.start_worker(
            forwarder.forward, source, destination, "synthetic", "client->server"
        )
        self.assertTrue(destination.write_timed_out.wait(3), "no downstream timeout observed")

        forwarder.shutdown_flag.set()

        self.assert_worker_finished(relay)

    def test_handler_configures_timeouts_and_joins_started_workers_before_close(self):
        for second_start_fails in (False, True):
            with self.subTest(second_start_fails=second_start_fails):
                events = []
                timeouts = {}
                client = mock.Mock()
                server = mock.Mock()
                workers = [mock.Mock(ident=None), mock.Mock(ident=None)]

                def configure_socket(sock, name):
                    sock.settimeout.side_effect = lambda value: timeouts.update({name: value})
                    sock.close.side_effect = lambda: events.append(("close", name))

                configure_socket(client, "client")
                configure_socket(server, "server")

                def start_worker(index):
                    # The first worker can read one socket and write the other.
                    self.assertEqual(timeouts, {"client": 1.0, "server": 1.0})
                    if index == 1 and second_start_fails:
                        raise RuntimeError("synthetic worker startup failure")
                    workers[index].ident = index + 1
                    events.append(("start", index))

                for index, worker in enumerate(workers):
                    worker.start.side_effect = lambda index=index: start_worker(index)
                    worker.join.side_effect = lambda index=index: events.append(("join", index))

                with mock.patch.object(forwarder.socket, "AF_VSOCK", 40, create=True), \
                        mock.patch.object(forwarder.socket, "socket", return_value=server), \
                        mock.patch.object(forwarder.threading, "Thread", side_effect=workers), \
                        self.assertLogs(level="INFO"):
                    forwarder.handle_connection(client, ("127.0.0.1", 12345), 3, 8001, "synthetic")

                first_close = next(index for index, event in enumerate(events) if event[0] == "close")
                for index in range(1 if second_start_fails else 2):
                    joins = [position for position, event in enumerate(events) if event == ("join", index)]
                    self.assertTrue(joins, "a started worker was not joined")
                    self.assertLess(max(joins), first_close)
                if second_start_fails:
                    workers[1].join.assert_not_called()
                client.close.assert_called_once()
                server.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
