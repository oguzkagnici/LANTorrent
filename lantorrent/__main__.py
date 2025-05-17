#!/usr/bin/env python3
"""
Entry point for running the LAN torrent system as a package.
"""
from lantorrent.core import main

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated by user")