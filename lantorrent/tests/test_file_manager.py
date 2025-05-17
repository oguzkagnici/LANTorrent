# tests/test_file_manager.py
import tempfile
import unittest
from pathlib import Path
import shutil

from lantorrent.core import FileManager


class TestFileManager(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for testing
        self.share_dir = Path(tempfile.mkdtemp())
        self.download_dir = Path(tempfile.mkdtemp())
        self.file_manager = FileManager(self.share_dir, self.download_dir)

        # Create a test file
        self.test_file = self.share_dir / "testfile.txt"
        with open(self.test_file, "wb") as f:
            f.write(b"Hello, world!" * 1000)  # ~13KB file

        # Force file scan
        self.file_manager._scan_shared_files()

    def tearDown(self):
        # Clean up temporary directories
        shutil.rmtree(self.share_dir)
        shutil.rmtree(self.download_dir)

    def test_scan_shared_files(self):
        # Check that the file was found
        self.assertEqual(len(self.file_manager.shared_files), 1)

        # Get the file hash (first key in shared_files dict)
        file_hash = next(iter(self.file_manager.shared_files.keys()))
        file_info = self.file_manager.shared_files[file_hash]

        # Verify file info
        self.assertEqual(file_info.name, "testfile.txt")
        self.assertGreater(file_info.size, 0)

    def test_get_chunk(self):
        # Get file hash
        file_hash = next(iter(self.file_manager.shared_files.keys()))

        # Request first chunk
        chunk = self.file_manager.get_chunk(file_hash, 0)
        self.assertIsNotNone(chunk)
        self.assertGreater(len(chunk), 0)