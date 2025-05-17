# lantorrent/core/peer_manager.py
import random
import socket
import time
import uuid
from typing import Dict, List

import logging
from .models import PeerInfo, PEER_TIMEOUT, TCP_BASE_PORT

logger = logging.getLogger('lantorrent.peer_manager')


class PeerManager:
    """Manages all known peers and their information."""

    def __init__(self):
        self.peers: Dict[str, PeerInfo] = {}
        self.my_id = str(uuid.uuid4())
        self.my_ip = self._get_local_ip()
        self.my_port = random.randint(TCP_BASE_PORT, TCP_BASE_PORT + 1000)
        logger.info(f"Initialized peer {self.my_id} at {self.my_ip}:{self.my_port}")

    def _get_local_ip(self) -> str:
        """Get the local IP address."""
        try:
            # Create a socket to determine the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback to a generic local IP
            return "127.0.0.1"

    def add_or_update_peer(self, peer_id: str, ip: str, port: int, files: List[str] = None) -> None:
        """Add a new peer or update an existing one."""
        if peer_id == self.my_id:
            return

        now = time.time()
        if peer_id in self.peers:
            self.peers[peer_id].last_seen = now
            if files is not None:
                self.peers[peer_id].files = files
        else:
            self.peers[peer_id] = PeerInfo(
                id=peer_id,
                ip=ip,
                port=port,
                last_seen=now,
                files=files or []
            )
            logger.info(f"New peer discovered: {peer_id} at {ip}:{port}")

    def remove_stale_peers(self) -> None:
        """Remove peers that haven't been seen recently."""
        now = time.time()
        stale_peers = [pid for pid, p in self.peers.items() if now - p.last_seen > PEER_TIMEOUT]
        for pid in stale_peers:
            logger.info(f"Removing stale peer: {pid}")
            del self.peers[pid]

    def get_best_peers(self, file_hash: str, count: int = 3) -> List[str]:
        """Get the best peers for downloading a specific file based on the tit-for-tat algorithm."""
        # Find peers that have the file
        candidate_peers = [
            pid for pid, peer in self.peers.items() if file_hash in peer.files
        ]

        if not candidate_peers:
            return []

        # Sort peers by upload contribution (tit-for-tat)
        # Prioritize peers who've uploaded more to us
        sorted_peers = sorted(
            candidate_peers,
            key=lambda pid: self.peers[pid].upload_bytes,
            reverse=True
        )

        return sorted_peers[:count]