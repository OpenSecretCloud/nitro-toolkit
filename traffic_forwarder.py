import socket
import sys
import threading
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def forward(source, destination):
    string = ' '
    while string:
        try:
            string = source.recv(1024)
            if string:
                logging.info(f"Forwarding {len(string)} bytes")
                destination.sendall(string)
            else:
                source.shutdown(socket.SHUT_RD)
                destination.shutdown(socket.SHUT_WR)
        except Exception as e:
            logging.error(f"Forwarding error: {e}")
            break

def server(local_ip, local_port, remote_cid, remote_port):
    try:
        dock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dock_socket.bind((local_ip, local_port))
        dock_socket.listen(5)
        logging.info(f"Listening on {local_ip}:{local_port}")

        while True:
            client_socket = dock_socket.accept()[0]
            logging.info(f"Accepted connection from {client_socket.getpeername()}")

            server_socket = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            try:
                server_socket.connect((remote_cid, remote_port))
                logging.info(f"Connected to VSOCK {remote_cid}:{remote_port}")
            except Exception as e:
                logging.error(f"Failed to connect to VSOCK: {e}")
                client_socket.close()
                continue

            outgoing_thread = threading.Thread(target=forward, args=(client_socket, server_socket))
            incoming_thread = threading.Thread(target=forward, args=(server_socket, client_socket))

            outgoing_thread.start()
            incoming_thread.start()
    except Exception as e:
        logging.error(f"Server error: {e}")
    finally:
        logging.info("Restarting server...")
        new_thread = threading.Thread(target=server, args=(local_ip, local_port, remote_cid, remote_port))
        new_thread.start()

def main(args):
    local_ip = str(args[0])
    local_port = int(args[1])
    remote_cid = int(args[2])
    remote_port = int(args[3])

    logging.info(f"Starting forwarder on {local_ip}:{local_port} to {remote_cid}:{remote_port}")
    server(local_ip, local_port, remote_cid, remote_port)

if __name__ == '__main__':
    main(sys.argv[1:])