To run the app, multiple terminals are required for each device.

1. **Terminal 1**: Start the server
   ```bash
   python3 -m lantorrent start
   ```

2. **Terminal 2**: Running the commands (Not in order, just examples)
   ```bash
    python3 -m lantorrent share path/to/file
    python3 -m lantorrent list
    python3 -m lantorrent download <file_hash>
    python3 -m lantorrent status
    ```