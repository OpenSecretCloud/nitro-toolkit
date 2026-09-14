import socket
import sys
import threading
import logging
import signal
import os
import errno

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global flag for graceful shutdown
shutdown_flag = threading.Event()

def signal_handler(sig, frame):
    logging.info("Received shutdown signal")
    shutdown_flag.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def shutdown_socket(sock, how):
    """Best-effort shutdown; the connection owner closes sockets after joining."""
    try:
        sock.shutdown(how)
    except OSError:
        pass


def forward(source, destination, connection_id, direction, stop_event=None):
    """Forward in one direction, retaining unsent bytes across polling timeouts."""
    if stop_event is None:
        stop_event = threading.Event()
    operation = "recv"
    bytes_forwarded = 0
    eof = False
    try:
        while not shutdown_flag.is_set() and not stop_event.is_set():
            operation = "recv"
            try:
                data = source.recv(1024)
            except socket.timeout:
                continue
            if not data:
                eof = True
                logging.info("Connection %s: End of data stream (%s)", connection_id, direction)
                break

            # sendall() does not report how much it sent when it times out.
            # Each send() reports progress, so a slow reader cannot make us
            # discard a suffix or replay bytes that were already written.
            operation = "send"
            pending = memoryview(data)
            while pending and not shutdown_flag.is_set() and not stop_event.is_set():
                try:
                    sent = destination.send(pending)
                except socket.timeout:
                    continue
                if sent == 0:
                    raise ConnectionResetError(errno.ECONNRESET, "Socket write made no progress")
                bytes_forwarded += sent
                pending = pending[sent:]
    except OSError as e:
        if not shutdown_flag.is_set() and not stop_event.is_set():
            # These report a peer disconnect, not an application request outcome.
            log = logging.info if e.errno in (errno.EPIPE, errno.ECONNRESET) else logging.error
            reason = "Peer disconnected" if e.errno in (errno.EPIPE, errno.ECONNRESET) else "Forwarding error"
            log("Connection %s: %s direction=%s operation=%s errno=%s bytes_forwarded=%d",
                connection_id, reason, direction, operation, e.errno, bytes_forwarded)
        stop_event.set()
    except Exception:
        if not shutdown_flag.is_set() and not stop_event.is_set():
            logging.error("Connection %s: Forwarding error direction=%s operation=%s reason=unexpected_exception",
                          connection_id, direction, operation)
        stop_event.set()
    finally:
        if eof and not shutdown_flag.is_set() and not stop_event.is_set():
            # A clean EOF is a half-close. The other direction may still have
            # a response to deliver; do not close its reading or writing side.
            shutdown_socket(destination, socket.SHUT_WR)
        else:
            stop_event.set()
            # A failed direction cannot make further progress. Wake its peer
            # worker even if that worker is waiting on an otherwise idle socket.
            shutdown_socket(source, socket.SHUT_RDWR)
            shutdown_socket(destination, socket.SHUT_RDWR)
        logging.info(f"Connection {connection_id}: Completed ({direction})")

def handle_connection(client_socket, client_addr, remote_cid, remote_port, connection_id):
    """Handle a single connection with proper resource management"""
    server_socket = None
    threads = []
    stop_event = threading.Event()
    
    try:
        # Connect to VSOCK
        server_socket = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        server_socket.settimeout(30)  # 30 second timeout for connection
        server_socket.connect((remote_cid, remote_port))
        logging.info(f"Connection {connection_id}: Connected to VSOCK {remote_cid}:{remote_port}")
        # Each socket is read by one worker and written by the other. Configure
        # polling before either starts, rather than racing to change its timeout.
        client_socket.settimeout(1.0)
        server_socket.settimeout(1.0)
        
        # Create forwarding threads
        outgoing_thread = threading.Thread(
            target=forward, 
            args=(client_socket, server_socket, connection_id, "client->server", stop_event),
            name=f"forward-{connection_id}-out"
        )
        incoming_thread = threading.Thread(
            target=forward, 
            args=(server_socket, client_socket, connection_id, "server->client", stop_event),
            name=f"forward-{connection_id}-in"
        )
        
        threads = [outgoing_thread, incoming_thread]
        
        # Start threads
        for thread in threads:
            thread.start()
        
        # Wait for threads to complete
        for thread in threads:
            thread.join()
            
    except Exception as e:
        logging.error("Connection %s: Connection setup failed errno=%s",
                      connection_id, e.errno if isinstance(e, OSError) else None)
    finally:
        stop_event.set()
        # Also cover partial worker startup. Sockets must outlive any worker
        # that started successfully, including when its partner could not start.
        for sock in [client_socket, server_socket]:
            if sock:
                shutdown_socket(sock, socket.SHUT_RDWR)
        for thread in threads:
            if thread.ident is not None:
                thread.join()
        # Now that both forwarding threads are done, we can fully close the sockets
        for sock in [client_socket, server_socket]:
            if sock:
                try:
                    # Both directions should already be shutdown by the forwarding threads
                    sock.close()
                except OSError:
                    pass  # Already closed
        
        logging.info(f"Connection {connection_id}: Handler complete")

def server(local_ip, local_port, remote_cid, remote_port):
    """Main server with proper resource management and graceful shutdown"""
    dock_socket = None
    connection_counter = 0
    active_connections = []
    
    try:
        dock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dock_socket.bind((local_ip, local_port))
        dock_socket.listen(5)
        dock_socket.settimeout(1.0)  # Check for shutdown every second
        logging.info(f"Listening on {local_ip}:{local_port}")
        
        while not shutdown_flag.is_set():
            try:
                client_socket, client_addr = dock_socket.accept()
                connection_counter += 1
                # Many helpers share one enclave log stream. Include only safe
                # process/endpoint metadata, never traffic or request contents.
                connection_id = f"{os.getpid()}:{connection_counter}@{remote_cid}:{remote_port}"
                logging.info(f"Connection {connection_id}: Accepted from {client_addr}")
                
                # Handle connection in a separate thread
                handler_thread = threading.Thread(
                    target=handle_connection,
                    args=(client_socket, client_addr, remote_cid, remote_port, connection_id),
                    name=f"handler-{connection_id}"
                )
                handler_thread.daemon = True  # Allow main thread to exit
                handler_thread.start()
                active_connections.append(handler_thread)
                
                # Clean up finished threads
                active_connections = [t for t in active_connections if t.is_alive()]
                
            except socket.timeout:
                continue  # Check shutdown flag
            except Exception as e:
                if not shutdown_flag.is_set():
                    logging.error(f"Server error: {e}")
                    
    except Exception as e:
        logging.error(f"Failed to start server: {e}")
    finally:
        if dock_socket:
            try:
                dock_socket.close()
            except:
                pass
        
        # Wait for active connections to finish
        logging.info("Waiting for active connections to close...")
        for thread in active_connections:
            thread.join(timeout=5)
        
        logging.info("Server shutdown complete")

def main(args):
    if len(args) < 4:
        logging.error("Usage: python traffic_forwarder.py <local_ip> <local_port> <remote_cid> <remote_port>")
        sys.exit(1)
        
    local_ip = str(args[0])
    local_port = int(args[1])
    remote_cid = int(args[2])
    remote_port = int(args[3])
    
    logging.info(f"Starting forwarder on {local_ip}:{local_port} to {remote_cid}:{remote_port}")
    
    # Run server (will block until shutdown)
    server(local_ip, local_port, remote_cid, remote_port)
    
    logging.info("Traffic forwarder exiting")

if __name__ == '__main__':
    main(sys.argv[1:])
