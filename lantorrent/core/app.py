# lantorrent/core/app.py
import asyncio
import hashlib
import logging
import socket
import time
from pathlib import Path
from typing import Optional, Dict, Set

from .discovery import MulticastDiscovery
from .file_manager import FileManager
from .peer_manager import PeerManager
from .transfer import TransferProtocol
from .models import FileInfo

logger = logging.getLogger('lantorrent.app')

class LANTorrent:
    """Main application class for LAN Torrent."""

    def __init__(self, share_dir='./lantorrent/shared', download_dir='./lantorrent/downloads'):
        # Initialize paths
        self.share_dir = Path(share_dir)
        self.download_dir = Path(download_dir)
        self.share_dir.mkdir(exist_ok=True)
        self.download_dir.mkdir(exist_ok=True)

        # Create components
        self.peer_manager = PeerManager()
        self.file_manager = FileManager(self.share_dir, self.download_dir)
        self.discovery = MulticastDiscovery(self.peer_manager, self.file_manager)
        self.transfer = TransferProtocol(self.peer_manager, self.file_manager)

        # Flags
        self.running = False
        self.tasks = []

    async def start(self):
        """Start all system components."""
        if self.running:
            logger.warning("LAN Torrent is already running")
            return

        self.running = True

        # Start components
        discovery_task = asyncio.create_task(self.discovery.start())
        transfer_task = asyncio.create_task(self.transfer.start())

        self.tasks = [discovery_task, transfer_task]
        logger.info("LAN Torrent started")

    async def stop(self):
        """Stop all system components."""
        if not self.running:
            return

        self.running = False

        # Stop components
        await self.discovery.stop()
        await self.transfer.stop()

        # Cancel tasks
        for task in self.tasks:
            task.cancel()

        self.tasks = []
        logger.info("LAN Torrent stopped")

    async def download_file(self, file_hash: str) -> bool:
        """Start downloading a file."""
        return await self.transfer.download_file(file_hash)

    def get_status(self) -> dict:
        """Get the current status of the application."""
        downloading = {
            file_hash: {
                'name': file_info.name,
                'size': file_info.size,
                'progress': len(self.file_manager.completed_chunks.get(file_hash, set())) / file_info.chunks
            }
            for file_hash, file_info in self.file_manager.downloading_files.items()
        }

        shared = {
            file_hash: {
                'name': file_info.name,
                'size': file_info.size
            }
            for file_hash, file_info in self.file_manager.shared_files.items()
        }

        peers = {
            pid: {
                'ip': peer.ip,
                'port': peer.port,
                'files': len(peer.files),
                'upload': peer.upload_bytes,
                'download': peer.download_bytes
            }
            for pid, peer in self.peer_manager.peers.items()
        }

        return {
            'id': self.peer_manager.my_id,
            'address': f"{self.peer_manager.my_ip}:{self.peer_manager.my_port}",
            'peers': peers,
            'shared_files': shared,
            'downloading': downloading
        }


async def main():
    """Main entry point for the LAN Torrent application."""
    import argparse
    import os
    import json
    import struct
    import tempfile

    # Socket path
    SOCKET_PATH = os.path.join(tempfile.gettempdir(), 'lantorrent.sock')

    parser = argparse.ArgumentParser(description='LAN Torrent - P2P file sharing for local networks')
    parser.add_argument('--share-dir', type=str, default='shared', help='Directory for shared files')
    parser.add_argument('--download-dir', type=str, default='downloads', help='Directory for downloaded files')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Start command
    start_parser = subparsers.add_parser('start', help='Start the LAN Torrent service')

    # Share command
    share_parser = subparsers.add_parser('share', help='Share a new file')
    share_parser.add_argument('file', type=str, help='Path to file to share')

    # List command
    list_parser = subparsers.add_parser('list', help='List available files')

    # Download command
    download_parser = subparsers.add_parser('download', help='Download a file')
    download_parser.add_argument('hash', type=str, help='Hash of the file to download')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show status information')

    args = parser.parse_args()

    # Set log level
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    # SERVER MODE (start command)
    if args.command == 'start' or not args.command:
        # Initialize the application
        app = LANTorrent(args.share_dir, args.download_dir)

        # Check if already running
        if os.path.exists(SOCKET_PATH):
            try:
                # Try to connect
                reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
                writer.close()
                await writer.wait_closed()
                logger.error("LAN Torrent is already running. Use commands to interact with it.")
                return
            except ConnectionRefusedError:
                # Socket exists but server is not running
                os.unlink(SOCKET_PATH)

        try:
            # Start the application
            await app.start()
            logger.info("LAN Torrent started. Press Ctrl+C to stop.")

            # Function to handle incoming command connections
            async def handle_command(reader, writer):
                try:
                    # Read command length
                    length_bytes = await reader.readexactly(4)
                    length = struct.unpack('!I', length_bytes)[0]

                    # Read command data
                    data = await reader.readexactly(length)
                    command_data = json.loads(data.decode('utf-8'))

                    # Process command
                    result = await process_command(app, command_data)

                    # Send response
                    response_json = json.dumps(result).encode('utf-8')
                    writer.write(struct.pack('!I', len(response_json)))
                    writer.write(response_json)
                    await writer.drain()
                except Exception as e:
                    logger.error(f"Error handling command: {e}")
                finally:
                    writer.close()
                    await writer.wait_closed()

            # Start command server
            server = await asyncio.start_unix_server(handle_command, SOCKET_PATH)
            os.chmod(SOCKET_PATH, 0o777)  # Make socket accessible to all users

            # Run both the server and app tasks
            await asyncio.gather(server.serve_forever(), *app.tasks)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            await app.stop()
            if os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)



    # CLIENT MODE (all other commands)
    else:
        # Check if server is running
        if not os.path.exists(SOCKET_PATH):
            logger.error("LAN Torrent is not running. Start it with 'python3 -m lantorrent start'")
            return

        try:
            # Connect to the running instance
            reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)

            # Prepare command data
            command_data = {
                'command': args.command,
                'args': vars(args)
            }

            # Send command
            data = json.dumps(command_data).encode('utf-8')
            writer.write(struct.pack('!I', len(data)))
            writer.write(data)
            await writer.drain()

            # Read response
            length_bytes = await reader.readexactly(4)
            length = struct.unpack('!I', length_bytes)[0]
            response_data = await reader.readexactly(length)
            response = json.loads(response_data.decode('utf-8'))

            # Close connection
            writer.close()
            await writer.wait_closed()

            # Display response
            if 'output' in response:
                print(response['output'])

            if 'success' in response and not response['success']:
                logger.error(response.get('error', 'Unknown error'))
                return 1

        except ConnectionRefusedError:
            logger.error("Cannot connect to LAN Torrent. The service may have crashed.")
            if os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)
            return 1
        except Exception as e:
            logger.error(f"Error communicating with LAN Torrent: {e}")
            return 1


# Function to process commands on the server side
async def process_command(app, command_data):
    command = command_data['command']
    args = command_data['args']

    try:
        if command == 'share':
            return await handle_share_command(app, args)
        elif command == 'list':
            return await handle_list_command(app)
        elif command == 'download':
            return await handle_download_command(app, args)
        elif command == 'status':
            return await handle_status_command(app)
        else:
            return {'success': False, 'error': f"Unknown command: {command}"}
    except Exception as e:
        logger.error(f"Error processing command {command}: {e}")
        return {'success': False, 'error': str(e)}


async def handle_share_command(app, args):
    import shutil

    src_path = Path(args['file'])
    if not src_path.exists():
        return {'success': False, 'error': f"File not found: {src_path}"}

    dst_path = Path(args['share_dir']) / src_path.name
    Path(args['share_dir']).mkdir(exist_ok=True, parents=True)

    shutil.copy2(src_path, dst_path)

    # Force scan of shared files
    app.file_manager._scan_shared_files()

    return {
        'success': True,
        'output': f"File shared: {src_path.name}\nPath: {dst_path}"
    }


async def handle_list_command(app):
    output = []
    output.append("\nAvailable files on the network:")
    output.append("=" * 70)
    output.append(f"{'Hash':<40} {'Size':<10} {'Peers':<6} Name")
    output.append("-" * 70)

    # Collect files from all peers
    all_files = {}
    for peer_id, peer in app.peer_manager.peers.items():
        for file_hash in peer.files:
            if file_hash not in all_files:
                all_files[file_hash] = {'peers': set(), 'name': 'Unknown', 'size': 0}
            all_files[file_hash]['peers'].add(peer_id)

    # Add local files
    for file_hash, file_info in app.file_manager.shared_files.items():
        if file_hash not in all_files:
            all_files[file_hash] = {'peers': set(), 'name': file_info.name, 'size': file_info.size}
        else:
            all_files[file_hash]['name'] = file_info.name
            all_files[file_hash]['size'] = file_info.size

    # Print the list
    for file_hash, info in all_files.items():
        #hash_short = file_hash[:8] + "..."
        size_str = f"{info['size'] / 1024 / 1024:.1f}MB" if info[
                                                                'size'] >= 1024 * 1024 else f"{info['size'] / 1024:.1f}KB"
        #output.append(f"{hash_short:<10} {size_str:<10} {len(info['peers']):<6} {info['name']}")
        output.append(f"{file_hash:<40} {size_str:<10} {len(info['peers']):<6} {info['name']}")

    output.append("\nTo download: python3 -m lantorrent download <hash>")

    return {
        'success': True,
        'output': "\n".join(output)
    }


async def handle_download_command(app, args):
    file_hash = args['hash']
    result = await app.download_file(file_hash)

    if result:
        return {
            'success': True,
            'output': f"Download started for file {file_hash}\nUse 'python3 -m lantorrent status' to check progress"
        }
    else:
        return {
            'success': False,
            'error': f"Failed to start download for file {file_hash}"
        }


async def handle_status_command(app):
    status = app.get_status()

    output = []
    output.append("\nLAN Torrent Status")
    output.append("=" * 70)
    output.append(f"Peer ID: {status['id']}")
    output.append(f"Address: {status['address']}")

    # Peers
    output.append("\nPeers:")
    output.append("-" * 70)
    for pid, pinfo in status['peers'].items():
        output.append(f"{pid}: {pinfo['ip']}:{pinfo['port']} - {pinfo['files']} files - "
                      f"Up: {pinfo['upload'] / 1024:.1f}KB, Down: {pinfo['download'] / 1024:.1f}KB")

    # Shared files
    output.append("\nShared Files:")
    output.append("-" * 70)
    for fid, finfo in status['shared_files'].items():
        size_str = f"{finfo['size'] / 1024 / 1024:.1f}MB" if finfo[
                                                                 'size'] >= 1024 * 1024 else f"{finfo['size'] / 1024:.1f}KB"
        output.append(f"{fid[:8]}... {size_str} {finfo['name']}")

    # Downloads
    output.append("\nDownloads:")
    output.append("-" * 70)
    for fid, finfo in status['downloading'].items():
        size_str = f"{finfo['size'] / 1024 / 1024:.1f}MB" if finfo[
                                                                 'size'] >= 1024 * 1024 else f"{finfo['size'] / 1024:.1f}KB"
        progress_str = f"{finfo['progress'] * 100:.1f}%"
        output.append(f"{fid[:8]}... {size_str} {progress_str} {finfo['name']}")

    return {
        'success': True,
        'output': "\n".join(output)
    }