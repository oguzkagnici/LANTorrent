# lantorrent/core/transfer.py
import asyncio
import json
import logging
import random
import struct
import time
from typing import Optional

from .models import MessageType, FileInfo, ChunkRequest, CHUNK_TIMEOUT, MAX_PARALLEL_CHUNKS, BEST_PEERS_COUNT

logger = logging.getLogger('lantorrent.transfer')


class TransferProtocol:
    """Handles file transfer protocol between peers."""

    def __init__(self, peer_manager, file_manager):
        self.peer_manager = peer_manager
        self.file_manager = file_manager
        self.server = None
        self.running = False
        self.active_transfers = set()

    async def start(self):
        """Start the transfer protocol server."""
        self.server = await asyncio.start_server(
            self._handle_client,
            self.peer_manager.my_ip,
            self.peer_manager.my_port
        )
        self.running = True

        # Start background tasks
        asyncio.create_task(self._request_scheduler())

        logger.info(f"Transfer protocol server started on {self.peer_manager.my_ip}:{self.peer_manager.my_port}")

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        """Stop the transfer protocol server."""
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        logger.info("Transfer protocol server stopped")

    async def _handle_client(self, reader, writer):
        """Handle a client connection."""
        try:
            # Read the message header (length)
            header = await reader.readexactly(4)
            length = struct.unpack('!I', header)[0]

            # Read the message
            data = await reader.readexactly(length)
            message = json.loads(data.decode('utf-8'))

            msg_type = MessageType(message.get('type'))

            if msg_type == MessageType.FILE_REQUEST:
                await self._handle_file_request(message, writer)
            elif msg_type == MessageType.CHUNK_REQUEST:
                await self._handle_chunk_request(message, writer)
            elif msg_type == MessageType.PEER_STATS:
                await self._handle_peer_stats(message)

        except (asyncio.IncompleteReadError, json.JSONDecodeError) as e:
            logger.warning(f"Error handling client: {e}")

        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_file_request(self, message, writer):
        """Handle a file request from a peer."""
        file_hash = message.get('file_hash')

        if file_hash in self.file_manager.shared_files:
            file_info = self.file_manager.shared_files[file_hash][0]

            response = {
                'type': MessageType.FILE_LIST.value,
                'file_hash': file_hash,
                'name': file_info.name,
                'size': file_info.size,
                'chunks': file_info.chunks,
                'chunks_hash': file_info.chunks_hash
            }

            # Send the response
            data = json.dumps(response).encode('utf-8')
            writer.write(struct.pack('!I', len(data)))
            writer.write(data)
            await writer.drain()

    async def _handle_chunk_request(self, message, writer):
        """Handle a chunk request from a peer."""
        file_hash = message.get('file_hash')
        chunk_index = message.get('chunk_index')
        peer_id = message.get('peer_id')

        if peer_id not in self.peer_manager.peers:
            return

        chunk_data = self.file_manager.get_chunk(file_hash, chunk_index)

        if chunk_data:
            # Update peer stats
            self.peer_manager.peers[peer_id].upload_bytes += len(chunk_data)

            # Send the chunk
            header = {
                'type': MessageType.CHUNK_RESPONSE.value,
                'file_hash': file_hash,
                'chunk_index': chunk_index,
                'success': True
            }

            header_data = json.dumps(header).encode('utf-8')
            writer.write(struct.pack('!I', len(header_data)))
            writer.write(header_data)

            # Send the chunk data length and data
            writer.write(struct.pack('!I', len(chunk_data)))
            writer.write(chunk_data)

            await writer.drain()

    async def _handle_peer_stats(self, message):
        """Handle peer statistics updates."""
        peer_id = message.get('peer_id')
        upload_bytes = message.get('upload_bytes', 0)

        if peer_id in self.peer_manager.peers:
            self.peer_manager.peers[peer_id].upload_bytes = upload_bytes

    async def request_file(self, file_hash: str, peer_id: str) -> Optional[FileInfo]:
        """Request file information from a peer."""
        if peer_id not in self.peer_manager.peers:
            return None

        peer = self.peer_manager.peers[peer_id]

        try:
            # Connect to the peer
            reader, writer = await asyncio.open_connection(peer.ip, peer.port)

            # Send the file request
            message = {
                'type': MessageType.FILE_REQUEST.value,
                'file_hash': file_hash,
                'peer_id': self.peer_manager.my_id
            }

            data = json.dumps(message).encode('utf-8')
            writer.write(struct.pack('!I', len(data)))
            writer.write(data)
            await writer.drain()

            # Read the response
            header = await reader.readexactly(4)
            length = struct.unpack('!I', header)[0]

            data = await reader.readexactly(length)
            response = json.loads(data.decode('utf-8'))

            writer.close()
            await writer.wait_closed()

            file_info = None
            if response.get('type') == MessageType.FILE_LIST.value:
                # Create a FileInfo object
                file_info = FileInfo(
                    name=response.get('name'),
                    size=response.get('size'),
                    chunks=response.get('chunks'),
                    hash=file_hash,
                    chunks_hash=response.get('chunks_hash', []),
                    complete=False
                )

            return file_info

        except Exception as e:
            logger.error(f"Error requesting file from {peer_id}: {e}")
            return None

    async def request_chunk(self, chunk_request: ChunkRequest) -> bool:
        """Request a chunk from a peer."""
        peer_id = chunk_request.peer_id
        file_hash = chunk_request.file_hash
        chunk_index = chunk_request.chunk_index

        if peer_id not in self.peer_manager.peers:
            return False

        peer = self.peer_manager.peers[peer_id]

        try:
            # Connect to the peer
            reader, writer = await asyncio.open_connection(peer.ip, peer.port)

            # Send the chunk request
            message = {
                'type': MessageType.CHUNK_REQUEST.value,
                'file_hash': file_hash,
                'chunk_index': chunk_index,
                'peer_id': self.peer_manager.my_id
            }

            data = json.dumps(message).encode('utf-8')
            writer.write(struct.pack('!I', len(data)))
            writer.write(data)
            await writer.drain()

            # Read the header response
            header = await reader.readexactly(4)
            length = struct.unpack('!I', header)[0]

            header_data = await reader.readexactly(length)
            header_response = json.loads(header_data.decode('utf-8'))

            if header_response.get('success', False):
                # Read the chunk data length
                chunk_len_data = await reader.readexactly(4)
                chunk_length = struct.unpack('!I', chunk_len_data)[0]

                # Read the chunk data
                chunk_data = await reader.readexactly(chunk_length)

                # Close the connection
                writer.close()
                await writer.wait_closed()

                # Update peer stats
                self.peer_manager.peers[peer_id].download_bytes += len(chunk_data)

                # Send peer stats
                await self._send_peer_stats(peer)

                # Save the chunk
                return self.file_manager.save_chunk(file_hash, chunk_index, chunk_data)
            else:
                writer.close()
                await writer.wait_closed()
                return False

        except Exception as e:
            logger.error(f"Error requesting chunk {chunk_index} from {peer_id}: {e}")
            return False

    async def _send_peer_stats(self, peer):
        """Send our statistics to a peer."""
        try:
            reader, writer = await asyncio.open_connection(peer.ip, peer.port)

            message = {
                'type': MessageType.PEER_STATS.value,
                'peer_id': self.peer_manager.my_id,
                'upload_bytes': self.peer_manager.my_upload_bytes
            }

            data = json.dumps(message).encode('utf-8')
            writer.write(struct.pack('!I', len(data)))
            writer.write(data)
            await writer.drain()

            writer.close()
            await writer.wait_closed()

        except Exception as e:
            logger.debug(f"Error sending stats to peer {peer.id}: {e}")

    async def download_file(self, file_hash: str, auto_share: bool = True) -> bool:
        """Download a file from the network."""

        # Get the best peers
        available_peers = self.peer_manager.get_best_peers(file_hash, count=BEST_PEERS_COUNT, optimistic=True)

        if not available_peers:
            logger.warning(f"No peers available for file {file_hash}")
            return False

        # Get file info from one of the peers
        random.shuffle(available_peers)
        file_info = None

        for peer_id in available_peers:
            file_info = await self.request_file(file_hash, peer_id)
            if file_info:
                break

        if not file_info:
            logger.warning(f"Could not get file info for {file_hash}")
            return False

        # Start the download
        self.file_manager.auto_share = auto_share
        self.file_manager.start_file_download(file_info)

        # Create chunk requests for all chunks
        for i in range(file_info.chunks):
            # Choose a random peer that has this file
            peer_id = random.choice(available_peers)

            request = ChunkRequest(
                file_hash=file_hash,
                chunk_index=i,
                peer_id=peer_id,
                created_at=time.time()
            )

            self.active_transfers.add(request)

        return True

    async def _request_scheduler(self):
        """Background task to schedule chunk requests."""
        while self.running:
            try:
                await self._process_chunk_requests()
                await asyncio.sleep(0.1)  # Small delay to prevent CPU hogging
            except Exception as e:
                logger.error(f"Error in request scheduler: {e}")
                await asyncio.sleep(1)

    async def _process_chunk_requests(self):
        """Process pending chunk requests."""
        # Count active requests by file
        active_by_file = {}
        for req in self.active_transfers:
            if req.in_progress:
                active_by_file[req.file_hash] = active_by_file.get(req.file_hash, 0) + 1

        # Process requests
        current_time = time.time()
        tasks = []

        for request in list(self.active_transfers):
            # Remove completed requests
            if request.file_hash not in self.file_manager.downloading_files:
                self.active_transfers.remove(request)
                continue

            # Skip requests for chunks we already have
            if request.chunk_index in self.file_manager.completed_chunks.get(request.file_hash, set()):
                self.active_transfers.remove(request)
                continue

            # Check if request timed out
            if request.in_progress and (current_time - request.started_at > CHUNK_TIMEOUT):
                request.in_progress = False
                request.failures += 1

                # If too many failures, try a different peer
                if request.failures >= 3:
                    additional_peers = 3
                    available_peers = [
                        pid for pid in self.peer_manager.get_best_peers(request.file_hash, count=BEST_PEERS_COUNT + additional_peers, optimistic=True)
                        if  pid != request.peer_id
                    ]

                    if available_peers:
                        request.peer_id = random.choice(available_peers)
                        request.failures = 0
                    else:
                        # No alternative peers, keep trying with the same one
                        pass

            # Start new requests if not in progress
            if (not request.in_progress and
                    active_by_file.get(request.file_hash, 0) < MAX_PARALLEL_CHUNKS):
                # Mark as in progress
                request.in_progress = True
                request.started_at = current_time

                # Increment active count
                active_by_file[request.file_hash] = active_by_file.get(request.file_hash, 0) + 1

                # Create task to download the chunk
                task = asyncio.create_task(self._download_chunk(request))
                tasks.append(task)

        # Wait for all chunk download tasks to complete
        if tasks:
            await asyncio.gather(*tasks)

    async def _download_chunk(self, request: ChunkRequest):
        """Download a single chunk."""
        success = await self.request_chunk(request)

        if success:
            # Remove the request if successful
            self.active_transfers.discard(request)
        else:
            # Mark as failed but keep in queue for retry
            request.in_progress = False
            request.failures += 1
