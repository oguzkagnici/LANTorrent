# lantorrent/core/models.py
from dataclasses import dataclass
from enum import Enum
import time
from typing import List, Set

# Constants
VERSION = "1.0.0"
CHUNK_SIZE = 1024 * 256  # 256 KB chunks
MULTICAST_GROUP = "224.0.0.1"
MULTICAST_PORT = 4545
CHUNK_TIMEOUT = 30  # seconds
MAX_PARALLEL_CHUNKS = 5
PEER_TIMEOUT = 120  # seconds
TCP_BASE_PORT = 8000  # Base port for TCP connections
BEST_PEERS_COUNT = 1

class MessageType(Enum):
    """Types of messages that can be sent between peers."""
    ANNOUNCE = 1
    FILE_REQUEST = 2
    FILE_LIST = 3
    CHUNK_REQUEST = 4
    CHUNK_RESPONSE = 5
    PEER_STATS = 6

@dataclass
class FileInfo:
    """Information about a file."""
    name: str
    size: int
    chunks: int
    hash: str
    chunks_hash: List[str]
    complete: bool = False

@dataclass
class ChunkRequest:
    """Request for a file chunk."""
    file_hash: str
    chunk_index: int
    peer_id: str
    created_at: float
    in_progress: bool = False
    started_at: float = 0
    failures: int = 0

    def __hash__(self):
        return hash((self.file_hash, self.chunk_index))

@dataclass
class Peer:
    """Information about a peer."""
    id: str
    ip: str
    port: int
    files: Set[str]
    last_seen: float = 0
    upload_bytes: int = 0
    download_bytes: int = 0

@dataclass
class PeerInfo:
    """Detailed information about a peer."""
    id: str
    ip: str
    port: int
    last_seen: float
    files: dict[str, FileInfo]
    upload_bytes: int = 0
    download_bytes: int = 0