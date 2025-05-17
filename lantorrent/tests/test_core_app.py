# Basic integration test
import asyncio
import tempfile
import unittest
from pathlib import Path
import shutil

from lantorrent.core.app import LANTorrent


class TestLANTorrentIntegration(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for testing
        self.share_dir1 = Path(tempfile.mkdtemp())
        self.download_dir1 = Path(tempfile.mkdtemp())
        self.share_dir2 = Path(tempfile.mkdtemp())
        self.download_dir2 = Path(tempfile.mkdtemp())

        # Create a test file in first peer's share directory
        self.test_file = self.share_dir1 / "testfile.txt"
        with open(self.test_file, "wb") as f:
            f.write(b"Hello, world!" * 1000)

    def tearDown(self):
        # Clean up temporary directories
        shutil.rmtree(self.share_dir1)
        shutil.rmtree(self.download_dir1)
        shutil.rmtree(self.share_dir2)
        shutil.rmtree(self.download_dir2)

    async def test_file_transfer(self):
        # Start first peer
        app1 = LANTorrent(self.share_dir1, self.download_dir1)
        await app1.start()

        # Start second peer
        app2 = LANTorrent(self.share_dir2, self.download_dir2)
        await app2.start()

        # Wait for discovery
        await asyncio.sleep(3)

        # Get file hash from first peer
        file_hash = None
        for fhash in app1.file_manager.shared_files:
            file_hash = fhash
            break

        self.assertIsNotNone(file_hash)

        # Initiate download on second peer
        success = await app2.download_file(file_hash)
        self.assertTrue(success)

        # Wait for the transfer to complete
        max_wait = 30
        for _ in range(max_wait):
            await asyncio.sleep(1)
            if file_hash not in app2.file_manager.downloading_files:
                break

        # Verify file exists in download directory
        downloaded_file = self.download_dir2 / "testfile.txt"
        self.assertTrue(downloaded_file.exists())

        # Stop applications
        await app1.stop()
        await app2.stop()