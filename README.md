# "Rube TorrentBerg Machine" - A LAN Torrent Application

- Oğuz Kağnıcı / 2021400006
- Muhammed Erkam Gökcepınar / 2020400240 

## How To Run

To run the app on the gui, go to root directory of the project and execute
```
   python3 lantorrent/gui/main.py
```

- To run the app on the cli, multiple terminals are required for each device.

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

## Requirements

- Python 3.12 or higher
- Tkinter (for GUI)