# lantorrent/core/file_manager.py
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import FileInfo, ChunkRequest, CHUNK_SIZE

logger = logging.getLogger('lantorrent.file_manager')


class FileManager:
    """Manages file sharing, chunking, and integrity verification."""

    def __init__(self, share_dir: str, download_dir: str):
        self.share_dir = Path(share_dir)
        self.download_dir = Path(download_dir)
        self.shared_files: Dict[str, FileInfo] = {}
        self.downloading_files: Dict[str, FileInfo] = {}
        self.downloaded_files: Dict[str, (FileInfo, float)] = {}
        self.completed_chunks: Dict[str, Set[int]] = {}  # file_hash -> set of completed chunk indices
        self.active_requests: Dict[Tuple[str, int], ChunkRequest] = {}  # (file_hash, chunk_index) -> request

        # Create directories if they don't exist
        self.share_dir.mkdir(exist_ok=True, parents=True)
        self.download_dir.mkdir(exist_ok=True, parents=True)

        # Load shared files
        self._scan_shared_files()

    def _scan_shared_files(self) -> None:
        """Scan the share directory for files to share."""
        for file_path in self.share_dir.glob('**/*'):
            if file_path.is_file():
                self._add_shared_file(file_path)

    def _add_shared_file(self, file_path: Path) -> None:
        """Add a file to the list of shared files."""
        try:
            file_size = file_path.stat().st_size
            num_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

            # Calculate the file hash using content
            file_hash = self._calculate_file_hash(file_path)

            # Calculate chunk hashes
            chunk_hashes = []
            with open(file_path, 'rb') as f:
                for _ in range(num_chunks):
                    chunk_data = f.read(CHUNK_SIZE)
                    if not chunk_data:
                        break
                    chunk_hash = hashlib.sha1(chunk_data).hexdigest()
                    chunk_hashes.append(chunk_hash)

            self.shared_files[file_hash] = FileInfo(
                name=file_path.name,
                size=file_size,
                chunks=num_chunks,
                hash=file_hash,
                chunks_hash=chunk_hashes,
                complete=True
            )
            logger.info(f"Added shared file: {file_path.name} ({file_hash})")

        except Exception as e:
            logger.error(f"Error adding shared file {file_path}: {e}")

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate a hash based on file content rather than path."""
        hasher = hashlib.sha1()
        with open(file_path, 'rb') as f:
            # Read and update hash in chunks to handle large files
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()

    def get_shared_file_list(self) -> dict:
        """Get a dictionary of shared files with metadata.

        Returns:
            A dictionary mapping file hashes to file metadata.
        """
        shared_files = {}
        for file_hash, file_info in self.shared_files.items():
            if file_info.complete:
                shared_files[file_hash] = {
                    'name': file_info.name,
                    'size': file_info.size
                }
        return shared_files


    def start_file_download(self, file_info: FileInfo) -> None:
        """Start downloading a file."""
        file_hash = file_info.hash

        if file_hash in self.downloading_files:
            logger.warning(f"Already downloading {file_info.name}")
            return

        self.downloading_files[file_hash] = file_info
        self.completed_chunks[file_hash] = set()
        logger.info(f"Started download for {file_info.name} ({file_hash})")

    def save_chunk(self, file_hash: str, chunk_index: int, chunk_data: bytes) -> bool:
        """Save a downloaded chunk to disk."""
        if file_hash not in self.downloading_files:
            logger.warning(f"Received chunk for unknown file: {file_hash}")
            return False

        file_info = self.downloading_files[file_hash]

        # Verify chunk integrity
        chunk_hash = hashlib.sha1(chunk_data).hexdigest()
        if chunk_index < len(file_info.chunks_hash) and chunk_hash != file_info.chunks_hash[chunk_index]:
            logger.warning(f"Chunk integrity check failed for {file_hash} chunk {chunk_index}")
            return False

        # Save the chunk to the partial file
        temp_file_path = self.download_dir / f"{file_info.name}.part"
        try:
            with open(temp_file_path, 'r+b') as f:
                f.seek(chunk_index * CHUNK_SIZE)
                f.write(chunk_data)
        except FileNotFoundError:
            # Create the file if it doesn't exist
            with open(temp_file_path, 'wb') as f:
                f.seek(chunk_index * CHUNK_SIZE)
                f.write(chunk_data)

        # Mark the chunk as completed
        self.completed_chunks[file_hash].add(chunk_index)

        # Check if the download is complete
        if len(self.completed_chunks[file_hash]) == file_info.chunks:
            self._finalize_download(file_hash, getattr(self, 'auto_share', True))

        return True

    def _finalize_download(self, file_hash: str, auto_share: bool = True) -> None:
        """Finalize a completed download."""
        file_info = self.downloading_files[file_hash]
        temp_file_path = self.download_dir / f"{file_info.name}.part"
        final_file_path = self.download_dir / file_info.name

        # Rename the partial file to the final file
        os.rename(temp_file_path, final_file_path)

        # Update file status
        file_info.complete = True
        self.downloaded_files[file_hash] = (file_info, time.time())

        if auto_share:
            self.shared_files[file_hash] = file_info
            # Copy the file to the shared directory
            shared_path = self.share_dir / file_info.name
            try:
                import shutil
                shutil.copy2(final_file_path, shared_path)
                logger.info(f"File automatically shared: {file_info.name}")
            except Exception as e:
                logger.error(f"Error sharing downloaded file: {e}")

        del self.downloading_files[file_hash]
        del self.completed_chunks[file_hash]

        logger.info(f"Download completed for {file_info.name}")

    def get_chunk(self, file_hash: str, chunk_index: int) -> Optional[bytes]:
        """Get a chunk from a shared file."""
        if file_hash not in self.shared_files:
            return None

        file_info = self.shared_files[file_hash]
        file_path = self.share_dir / file_info.name

        if not file_path.exists() or chunk_index >= file_info.chunks:
            return None

        try:
            with open(file_path, 'rb') as f:
                f.seek(chunk_index * CHUNK_SIZE)
                return f.read(CHUNK_SIZE)
        except Exception as e:
            logger.error(f"Error reading chunk {chunk_index} from {file_path}: {e}")
            return None