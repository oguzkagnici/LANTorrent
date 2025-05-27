# lantorrent/core/app.py
import asyncio
import hashlib
import logging
import socket
import time
from pathlib import Path
from typing import Optional, Dict, Set
import os
import shutil

from .discovery import MulticastDiscovery
from .file_manager import FileManager
from .peer_manager import PeerManager
from .transfer import TransferProtocol
from .models import FileInfo
from .utils import format_size

logger = logging.getLogger('lantorrent.app')

class LANTorrent:
    """Main application class for LAN Torrent."""

    def __init__(self, share_dir='./lantorrent/shared', download_dir='./lantorrent/downloads'):
        # Initialize paths
        self.share_dir = Path(share_dir).resolve()
        self.download_dir = Path(download_dir).resolve()
        self.share_dir.mkdir(exist_ok=True, parents=True)
        self.download_dir.mkdir(exist_ok=True, parents=True)

        # Create components
        self.peer_manager = PeerManager()
        self.file_manager = FileManager(str(self.share_dir), str(self.download_dir))
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
        #logger.info("LAN Torrent started")

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
            if not task.done():
                task.cancel()
        
        # Wait for tasks to finish cancellation
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        self.tasks = []
        logger.info("LAN Torrent stopped")

    async def add_file_to_share(self, file_path_str: str) -> Optional[FileInfo]:
      """
      Adds a file to the shared files list.
      The file is copied to the application's share directory if it's not already there,
      under its original name or a unique variant if a conflict exists.
      The file is announced to peers.
      """
      original_file_path = Path(file_path_str).resolve()
      if not original_file_path.exists() or not original_file_path.is_file():
          logger.error(f"File not found or is not a file: {original_file_path}")
          return None

      original_file_name = original_file_path.name

      # Determine the actual path for storing the file in the share directory.
      dst_path = self.share_dir / original_file_name
      name_base, ext = os.path.splitext(original_file_name)
      counter = 1

      # Avoid overwriting existing files
      while dst_path.exists():
          new_name = f"{name_base}_{counter}{ext}"
          dst_path = self.share_dir / new_name
          counter += 1

      # If original file isn't already at the target location, copy it
      try:
          if original_file_path != dst_path:
              shutil.copy2(original_file_path, dst_path)
              logger.info(f"Copied file to share directory: {dst_path}")
          else:
              logger.info(f"File is already in share directory: {dst_path}")
      except Exception as e:
          logger.error(f"Failed to copy file: {e}")
          return None

      # Attempt to add the file to the shared list
      file_info = self.file_manager._add_shared_file(dst_path, original_file_name)

      if file_info:
          logger.info(f"Sharing new file: {file_info.name} (hash: {file_info.hash})")
          if self.running and self.discovery:
              self.discovery._send_announce()
              logger.info(f"Sent announce after adding {file_info.name}")
          return file_info
      else:
          # Cleanup copied file if adding failed
          if dst_path.exists() and dst_path != original_file_path:
              try:
                  os.remove(dst_path)
                  logger.info(f"Removed copied file due to failed share: {dst_path}")
              except Exception as cleanup_err:
                  logger.error(f"Failed to remove copied file: {dst_path}, error: {cleanup_err}")
          return None


    async def download_file(self, file_hash: str, auto_share: bool = True) -> bool:
        """Start downloading a file."""
        return await self.transfer.download_file(file_hash, auto_share)

    def get_status(self) -> dict:
        """Get the current status of the application."""
        downloading = {}
        if self.file_manager.downloading_files:
            for file_hash, file_info in self.file_manager.downloading_files.items():
                if file_info.chunks > 0:
                    progress = len(self.file_manager.completed_chunks.get(file_hash, set())) / file_info.chunks
                else:
                    progress = 1.0 if file_info.size == 0 else 0.0
                downloading[file_hash] = {
                    'name': file_info.name,
                    'size': file_info.size,
                    'progress': progress
                }

        downloaded = {
            file_hash: {
                'name': file_info.name,
                'size': file_info.size,
                'downloaded_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(downloaded_at))

            }
            for file_hash, (file_info, downloaded_at) in self.file_manager.downloaded_files.items()
        }

        shared = {
            file_hash: {
                'name': file_info.name,
                'size': file_info.size
            }
            for file_hash, (file_info, _) in self.file_manager.shared_files.items()
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
            'downloading': downloading,
            'downloaded': downloaded
        }


async def main():
    """Main entry point for the LAN Torrent application."""
    import argparse
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
    download_parser.add_argument('--no-share', action='store_true',
                                 help='Do not automatically share the downloaded file')

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
                _reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
                writer.close()
                await writer.wait_closed()
                logger.error("LAN Torrent is already running. Use commands to interact with it.")
                return
            except ConnectionRefusedError:
                # Socket exists but server is not running
                try:
                    os.unlink(SOCKET_PATH)
                except OSError as e:
                    logger.error(f"Failed to remove stale socket file {SOCKET_PATH}: {e}")


        try:
            # Start the application
            await app.start()

            print("\n" + "=" * 70)
            print("LAN Torrent service is now running in the background.")
            print(f"Share directory: {app.share_dir.resolve()}")
            print(f"Download directory: {app.download_dir.resolve()}")
            print("Open a new terminal window and run the following commands to use it:")
            print("  python3 -m lantorrent list          - List available files")
            print("  python3 -m lantorrent share path/to/file  - Share a file")
            print("  python3 -m lantorrent download <hash> - Download a file")
            print("  python3 -m lantorrent status        - Show peer/file status")
            print("For more information, run: python3 -m lantorrent --help")
            print("Press Ctrl+C to stop the service.")
            print("=" * 70 + "\n")

            # Function to handle incoming command connections
            async def handle_command_connection(reader, writer):
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
                except asyncio.IncompleteReadError:
                    logger.warning("Client disconnected before sending full command.")
                except json.JSONDecodeError:
                    logger.error("Failed to decode command from client.")
                except Exception as e:
                    logger.error(f"Error handling command: {e}", exc_info=True)
                finally:
                    writer.close()
                    await writer.wait_closed()

            # Start command server
            server = await asyncio.start_unix_server(handle_command_connection, SOCKET_PATH)
            try:
                os.chmod(SOCKET_PATH, 0o777)
            except OSError as e:
                 logger.warning(f"Could not chmod socket {SOCKET_PATH}: {e}. May cause issues if client runs as different user.")


            # Run both the server and app tasks
            async with server:
                await asyncio.gather(server.serve_forever(), *app.tasks)


        except KeyboardInterrupt:
            logger.info("Interrupted by user. Stopping LAN Torrent...")
        except Exception as e:
            logger.error(f"Unhandled exception in main server loop: {e}", exc_info=True)
        finally:
            logger.info("Shutting down LAN Torrent service...")
            await app.stop()
            if os.path.exists(SOCKET_PATH):
                try:
                    os.unlink(SOCKET_PATH)
                except OSError as e:
                    logger.error(f"Failed to remove socket file {SOCKET_PATH} on shutdown: {e}")
            logger.info("LAN Torrent service stopped.")


    # CLIENT MODE (all other commands)
    else:
        # Check if server is running
        if not os.path.exists(SOCKET_PATH):
            logger.error("LAN Torrent service is not running. Start it with 'python3 -m lantorrent start'")
            return 1

        try:
            # Connect to the running instance
            reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)

            # Prepare command data
            command_data_dict = {
                'command': args.command,
                'args': vars(args)
            }

            # Send command
            data_to_send = json.dumps(command_data_dict).encode('utf-8')
            writer.write(struct.pack('!I', len(data_to_send)))
            writer.write(data_to_send)
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
            if 'output' in response and response['output'] is not None:
                print(response['output'])

            if 'success' in response and not response['success']:
                logger.error(response.get('error', 'Unknown error from server'))
                return 1
            elif 'success' not in response:
                logger.error("Received malformed response from server.")
                return 1
            return 0

        except ConnectionRefusedError:
            logger.error("Cannot connect to LAN Torrent service. It may have crashed or is not running.")
            if os.path.exists(SOCKET_PATH):
                 try:
                    os.unlink(SOCKET_PATH)
                    logger.info(f"Removed potentially stale socket file: {SOCKET_PATH}")
                 except OSError as e:
                    logger.error(f"Could not remove stale socket {SOCKET_PATH}: {e}")
            return 1
        except asyncio.IncompleteReadError:
            logger.error("Connection to LAN Torrent service closed prematurely while awaiting response.")
            return 1
        except Exception as e:
            logger.error(f"Error communicating with LAN Torrent service: {e}", exc_info=True)
            return 1


# Function to process commands on the server side (called by handle_command_connection)
async def process_command(app: LANTorrent, command_data: dict):
    command = command_data.get('command')
    args_dict = command_data.get('args', {})

    try:
        if command == 'share':
            return await handle_share_command(app, args_dict)
        elif command == 'list':
            return await handle_list_command(app)
        elif command == 'download':
            return await handle_download_command(app, args_dict)
        elif command == 'status':
            return await handle_status_command(app)
        else:
            return {'success': False, 'error': f"Unknown command: {command}"}
    except Exception as e:
        logger.error(f"Error processing command '{command}': {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


async def handle_share_command(app: LANTorrent, args: dict):
    file_to_share_str = args.get('file')
    if not file_to_share_str:
        return {'success': False, 'error': "File path not provided for share command."}

    file_info = await app.add_file_to_share(file_to_share_str)

    if file_info:
        message = f"File shared: {file_info.name} (Hash: {file_info.hash})"
        return {
            'success': True,
            'output': message
        }
    else:
        return {
            'success': False,
            'error': f"Failed to share file: {file_to_share_str}. Check server logs for details."
        }



async def handle_list_command(app: LANTorrent):
    output = []
    output.append("\nAvailable files on the network:")
    output.append("=" * 70)
    output.append(f"{'Hash':<40} {'Size':<10} {'Peers':<6} Name")
    output.append("-" * 70)

    all_network_files = {}

    for f_hash, (f_info, _) in app.file_manager.shared_files.items():
        if f_hash not in all_network_files:
            all_network_files[f_hash] = {'name': f_info.name, 'size': f_info.size, 'peers': set()}
        else:
            all_network_files[f_hash]['name'] = f_info.name
            all_network_files[f_hash]['size'] = f_info.size
        all_network_files[f_hash]['peers'].add(app.peer_manager.my_id)

    for peer_id, peer_info in app.peer_manager.peers.items():
        if peer_id == app.peer_manager.my_id: continue

        for f_hash, announced_file_meta in peer_info.files.items():
            if f_hash not in all_network_files:
                all_network_files[f_hash] = {
                    'name': announced_file_meta.get('name', 'Unknown'),
                    'size': announced_file_meta.get('size', 0),
                    'peers': set()
                }
            all_network_files[f_hash]['peers'].add(peer_id)
    
    if not all_network_files:
        output.append("No files found on the network yet.")
    else:
        for file_hash, info in all_network_files.items():
            size_str = format_size(info['size'])
            output.append(f"{file_hash:<40} {size_str:<10} {len(info['peers']):<6} {info['name']}")

    output.append("\nTo download: python3 -m lantorrent download <hash>")

    return {
        'success': True,
        'output': "\n".join(output)
    }


async def handle_download_command(app: LANTorrent, args: dict):
    file_hash = args.get('hash')
    if not file_hash:
        return {'success': False, 'error': "File hash not provided for download."}
        
    auto_share = not args.get('no_share', False)
    result = await app.download_file(file_hash, auto_share)

    if result:
        return {
            'success': True,
            'output': f"Download started for file hash: {file_hash}\nUse 'python3 -m lantorrent status' to check progress."
        }
    else:
        return {
            'success': False,
            'error': f"Failed to start download for file hash: {file_hash}. File may not be available or hash is incorrect. Check server logs."
        }


async def handle_status_command(app: LANTorrent):
    status = app.get_status()

    output = []
    output.append("\nLAN Torrent Status")
    output.append("=" * 70)
    output.append(f"My Peer ID: {status.get('id', 'N/A')}")
    output.append(f"My Address: {status.get('address', 'N/A')}")
    output.append(f"Share Directory: {app.share_dir}")
    output.append(f"Download Directory: {app.download_dir}")


    # Peers
    output.append("\nConnected Peers:")
    output.append("-" * 70)
    if status.get('peers'):
        for pid, pinfo in status['peers'].items():
            output.append(f"  ID: {pid[:12]}... ({pinfo['ip']}:{pinfo['port']}) - Files: {pinfo['files']}, "
                          f"Up: {format_size(pinfo['upload'])}, Down: {format_size(pinfo['download'])}")
    else:
        output.append("  No other peers currently detected.")

    # Shared files
    output.append("\nMy Shared Files:")
    output.append("-" * 70)
    if status.get('shared_files'):
        for fid, finfo in status['shared_files'].items():
            size_str = format_size(finfo['size'])
            output.append(f"  {finfo['name']} ({size_str}) - Hash: {fid[:12]}...")
    else:
        output.append("  Not sharing any files.")

    # Downloading files
    output.append("\nDownloading Files:")
    output.append("-" * 70)
    if status.get('downloading'):
        for fid, finfo in status['downloading'].items():
            size_str = format_size(finfo['size'])
            progress_str = f"{finfo.get('progress', 0) * 100:.1f}%"
            output.append(f"  {finfo['name']} ({size_str}) - {progress_str} - Hash: {fid[:12]}...")
    else:
        output.append("  No files currently downloading.")

    # Completed downloads
    output.append("\nCompleted Downloads:")
    output.append("-" * 70)
    if status.get('downloaded'):
        for fid, finfo in status['downloaded'].items():
            size_str = format_size(finfo['size'])
            output.append(f"  {finfo['name']} ({size_str}) - Completed at {finfo['downloaded_at']} - Hash: {fid[:12]}...")
    else:
        output.append("  No files downloaded yet.")
    output.append("=" * 70)


    return {
        'success': True,
        'output': "\n".join(output)
    }