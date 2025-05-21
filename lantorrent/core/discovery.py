# lantorrent/core/discovery.py
import asyncio
import json
import logging
import socket
import struct
from typing import Tuple

from .models import MULTICAST_GROUP, MULTICAST_PORT, MessageType, VERSION

logger = logging.getLogger('lantorrent.discovery')


class MulticastDiscovery:
    """Handles peer discovery using UDP multicast."""

    def __init__(self, peer_manager, file_manager):
        self.peer_manager = peer_manager
        self.file_manager = file_manager
        self.socket = None
        self.running = False

    async def start(self):
        """Start the multicast discovery service."""
        # Create the UDP socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Bind to the multicast port
        self.socket.bind(('', MULTICAST_PORT))

        # Tell the kernel to join the multicast group
        group = socket.inet_aton(MULTICAST_GROUP)
        mreq = struct.pack('4sL', group, socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        # Set socket to non-blocking
        self.socket.setblocking(False)

        self.running = True
        logger.info(f"Multicast discovery started on {MULTICAST_GROUP}:{MULTICAST_PORT}")

        # Start to receive and announce tasks
        self.receive_task = asyncio.create_task(self._receive_loop())
        self.announce_task = asyncio.create_task(self._announce_loop())

        #await asyncio.gather(receive_task, announce_task)

    async def stop(self):
        """Stop the multicast discovery service."""
        self.running = False

        # Cancel the running tasks
        if hasattr(self, 'receive_task'):
            self.receive_task.cancel()
        if hasattr(self, 'announce_task'):
            self.announce_task.cancel()

        if self.socket:
            self.socket.close()
            self.socket = None
        logger.info("Multicast discovery stopped")

    async def _receive_loop(self):
        """Continuously receive and process multicast messages."""
        while self.running:
            try:
                # Create a future to receive data asynchronously
                loop = asyncio.get_running_loop()
                future = loop.create_future()

                # Add a reader callback for the socket
                loop.add_reader(self.socket.fileno(), self._socket_receive, future)

                # Wait for data
                data, addr = await future

                # Process the message
                try:
                    message = json.loads(data.decode('utf-8'))
                    msg_type = MessageType(message.get('type'))

                    if msg_type == MessageType.ANNOUNCE:
                        self._handle_announce(message, addr)

                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Invalid message received: {e}")

            except Exception as e:
                if self.running:
                    logger.error(f"Error in receive loop: {e}")
                    await asyncio.sleep(1)

    def _socket_receive(self, future):
        """Callback function to handle socket data availability."""
        loop = asyncio.get_running_loop()
        try:
            data, addr = self.socket.recvfrom(4096)
            loop.remove_reader(self.socket.fileno())
            if not future.cancelled():
                future.set_result((data, addr))
        except Exception as e:
            loop.remove_reader(self.socket.fileno())
            if not future.cancelled():
                future.set_exception(e)

    async def _announce_loop(self):
        """Periodically announce this peer's presence."""
        while self.running:
            try:
                self._send_announce()
                await asyncio.sleep(10)  # Announce every 10 seconds
            except Exception as e:
                logger.error(f"Error in announce loop: {e}")
                await asyncio.sleep(1)

    def _send_announce(self):
        """Send an announcement of this peer's presence."""
        message = {
            'type': MessageType.ANNOUNCE.value,
            'peer_id': self.peer_manager.my_id,
            'ip': self.peer_manager.my_ip,
            'port': self.peer_manager.my_port,
            'files': self.file_manager.get_shared_file_list(),
            'version': VERSION
        }

        # Send the message to the multicast group
        data = json.dumps(message).encode('utf-8')
        self.socket.sendto(data, (MULTICAST_GROUP, MULTICAST_PORT))

    def _handle_announce(self, message, addr):
        """Handle an announcement from another peer."""
        peer_id = message.get('peer_id')
        ip = message.get('ip')
        port = message.get('port')
        files = message.get('files', {})

        # Update the peer information
        self.peer_manager.add_or_update_peer(peer_id, ip, port, files)