import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import asyncio
import threading
from pathlib import Path
import logging
import sys
import os

# Adjust import path for core modules
# This assumes the script might be run from the project root (LANTorrent)
# or that the LANTorrent directory is in PYTHONPATH.
try:
    from lantorrent.core.app import LANTorrent
    from lantorrent.core.models import FileInfo
    from lantorrent.core.utils import format_size
except ImportError:
    # If running gui/main.py directly, try to add project root to path
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from lantorrent.core.app import LANTorrent
    from lantorrent.core.models import FileInfo
    from lantorrent.core.utils import format_size

logger = logging.getLogger('lantorrent.gui')

class LANTorrentAppUI:
    def __init__(self, root_tk):
        self.root = root_tk
        self.root.title("LAN Torrent")
        self.root.geometry("800x600")

        self.lantorrent_instance: LANTorrent | None = None
        self.async_loop = None
        self.async_thread = None

        # Configure logging for the GUI if not already configured
        if not logging.getLogger('lantorrent').hasHandlers():
            logging.basicConfig(level=logging.INFO,
                                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        controls_frame = ttk.LabelFrame(main_frame, text="Controls", padding="10")
        controls_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        self.start_button = ttk.Button(controls_frame, text="Start Service", command=self.start_service)
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.share_button = ttk.Button(controls_frame, text="Share File", command=self.ui_share_file, state=tk.DISABLED)
        self.share_button.pack(side=tk.LEFT, padx=5)

        self.download_button = ttk.Button(controls_frame, text="Download File", command=self.ui_prompt_download_file, state=tk.DISABLED)
        self.download_button.pack(side=tk.LEFT, padx=5)

        display_frame = ttk.LabelFrame(main_frame, text="Status & Files", padding="10")
        display_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1) # Ensure display_frame's column expands

        self.status_text = tk.Text(display_frame, height=15, width=80, state=tk.DISABLED, wrap=tk.WORD)
        status_scrollbar = ttk.Scrollbar(display_frame, orient=tk.VERTICAL, command=self.status_text.yview)
        self.status_text.config(yscrollcommand=status_scrollbar.set)
        
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)


        self.status_update_job = None

    def _run_async_loop(self, loop_to_run):
        asyncio.set_event_loop(loop_to_run)
        try:
            loop_to_run.run_forever()
        finally:
            loop_to_run.close()

    def start_service(self):
        if not self.lantorrent_instance:
            project_root = Path(__file__).resolve().parent.parent.parent
            share_dir = project_root / "lantorrent_gui_shared"
            download_dir = project_root / "lantorrent_gui_downloads"

            try:
                share_dir.mkdir(parents=True, exist_ok=True)
                download_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using share directory: {share_dir}")
                logger.info(f"Using download directory: {download_dir}")
            except OSError as e:
                messagebox.showerror("Directory Error", f"Could not create directories: {e}")
                return

            self.lantorrent_instance = LANTorrent(share_dir=str(share_dir), download_dir=str(download_dir))
            
            self.async_loop = asyncio.new_event_loop()
            self.async_thread = threading.Thread(target=self._run_async_loop, args=(self.async_loop,), daemon=True)
            self.async_thread.start()

            asyncio.run_coroutine_threadsafe(self.lantorrent_instance.start(), self.async_loop)

            self.start_button.config(text="Service Running", state=tk.DISABLED)
            self.share_button.config(state=tk.NORMAL)
            self.download_button.config(state=tk.NORMAL)
            messagebox.showinfo("Service", "LAN Torrent service started.")
            self.schedule_status_update()
        else:
            messagebox.showinfo("Service", "Service is already running.")

    def schedule_status_update(self):
        if self.lantorrent_instance and self.lantorrent_instance.running:
            self.update_status_display()
            self.status_update_job = self.root.after(5000, self.schedule_status_update)

    def update_status_display(self):
        if self.lantorrent_instance and self.lantorrent_instance.running:
            try:
                status_data = self.lantorrent_instance.get_status()
                display_content = f"My ID: {status_data.get('id', 'N/A')}\n"
                display_content += f"Address: {status_data.get('address', 'N/A')}\n\n"

                display_content += "Peers:\n"
                if status_data.get('peers'):
                    for pid, pdata in status_data['peers'].items():
                        display_content += f"  - ID: {pid[:8]}... ({pdata['ip']}:{pdata['port']}), Files: {pdata['files']}\n"
                else:
                    display_content += "  No peers connected.\n"
                display_content += "\n"

                display_content += "Shared Files (from this instance):\n"
                if status_data.get('shared_files'):
                    for fhash, fdata in status_data['shared_files'].items():
                        display_content += f"  - {fdata['name']} ({format_size(fdata['size'])})\n    Hash: {fhash}\n"
                else:
                    display_content += "  You are not sharing any files yet.\n"
                display_content += "\n"
                
                # Corrected logic for "Downloadable Files (from peers)"
                all_peer_files_display_info = {}
                if status_data.get('peers') and self.lantorrent_instance:
                    # First, collect all files and the set of peers that have them
                    files_and_their_peers = {} # f_hash -> {'name': name, 'size': size, 'peers': {peer_id1, peer_id2}}
                    
                    # Iterate through PeerInfo objects stored in peer_manager
                    for peer_id, peer_obj in self.lantorrent_instance.peer_manager.peers.items():
                        for f_hash, file_details_from_peer in peer_obj.files.items():
                            # Ensure file_details_from_peer is a dictionary with 'name' and 'size'
                            if not isinstance(file_details_from_peer, dict) or \
                               'name' not in file_details_from_peer or \
                               'size' not in file_details_from_peer:
                                logger.warning(f"Peer {peer_id} has malformed file data for hash {f_hash}: {file_details_from_peer}")
                                continue

                            if f_hash not in status_data.get('shared_files', {}): # Don't list our own files as downloadable from peers
                                if f_hash not in files_and_their_peers:
                                    files_and_their_peers[f_hash] = {
                                        'name': file_details_from_peer['name'],
                                        'size': file_details_from_peer['size'],
                                        'peers': set()
                                    }
                                files_and_their_peers[f_hash]['peers'].add(peer_id)
                    
                    # Now, populate all_peer_files_display_info with peer_count
                    for f_hash, data in files_and_their_peers.items():
                        all_peer_files_display_info[f_hash] = {
                            'name': data['name'],
                            'size': data['size'],
                            'peer_count': len(data['peers'])
                        }
                
                display_content += "Downloadable Files (from peers):\n"
                if all_peer_files_display_info:
                    for f_hash, display_info in all_peer_files_display_info.items():
                         display_content += f"  - {display_info['name']} ({format_size(display_info['size'])}) - Peers: {display_info['peer_count']}\n    Hash: {f_hash}\n"
                else:
                    display_content += "  No files discovered from peers yet.\n"
                display_content += "\n"

                display_content += "Downloading Files:\n"
                if status_data.get('downloading'):
                    for fhash, fdata in status_data['downloading'].items():
                        progress = fdata.get('progress', 0) * 100
                        display_content += f"  - {fdata['name']} ({format_size(fdata['size'])}) - {progress:.2f}%\n    Hash: {fhash}\n"
                else:
                    display_content += "  No files currently downloading.\n"
                display_content += "\n"

                display_content += "Downloaded Files:\n"
                if status_data.get('downloaded'):
                    for fhash, fdata in status_data['downloaded'].items():
                        display_content += f"  - {fdata['name']} ({format_size(fdata['size'])}) - At: {fdata['downloaded_at']}\n    Hash: {fhash}\n"
                else:
                    display_content += "  No files downloaded yet.\n"

                self.status_text.config(state=tk.NORMAL)
                self.status_text.delete(1.0, tk.END)
                self.status_text.insert(tk.END, display_content)
                self.status_text.config(state=tk.DISABLED)
            except Exception as e:
                logger.error(f"Error updating status display: {e}", exc_info=True)
                self.status_text.config(state=tk.NORMAL)
                self.status_text.delete(1.0, tk.END)
                self.status_text.insert(tk.END, f"Error updating status: {e}")
                self.status_text.config(state=tk.DISABLED)
        else:
            self.status_text.config(state=tk.NORMAL)
            self.status_text.delete(1.0, tk.END)
            self.status_text.insert(tk.END, "Service not running. Click 'Start Service'.")
            self.status_text.config(state=tk.DISABLED)

    def ui_share_file(self):
        if not self.lantorrent_instance or not self.lantorrent_instance.running or not self.async_loop:
            messagebox.showerror("Error", "Service not running. Please start the service first.")
            return

        filepath = filedialog.askopenfilename(title="Select file to share")
        if filepath:
            async def _task_share():
                try:
                    logger.info(f"Attempting to share file: {filepath}")
                    # Ensure add_file_to_share is available and callable
                    if hasattr(self.lantorrent_instance, 'add_file_to_share'):
                        file_info = await self.lantorrent_instance.add_file_to_share(filepath)
                        if file_info:
                            self.root.after(0, lambda: messagebox.showinfo("Share", f"File '{Path(filepath).name}' shared. Hash: {file_info.hash}"))
                            self.root.after(0, self.update_status_display)
                        else:
                            self.root.after(0, lambda: messagebox.showerror("Share", f"Failed to share file '{Path(filepath).name}'. Check logs."))
                    else:
                        self.root.after(0, lambda: messagebox.showerror("Error", "Core 'add_file_to_share' method not found."))
                except Exception as e:
                    logger.error(f"Error during share task: {e}", exc_info=True)
                    self.root.after(0, lambda: messagebox.showerror("Share Error", f"An error occurred: {e}"))

            if self.async_loop.is_running():
                 asyncio.run_coroutine_threadsafe(_task_share(), self.async_loop)
            else:
                 messagebox.showerror("Error", "Async loop not running for sharing.")

    def ui_prompt_download_file(self):
        if not self.lantorrent_instance or not self.lantorrent_instance.running or not self.async_loop:
            messagebox.showerror("Error", "Service not running. Please start the service first.")
            return

        file_hash = simpledialog.askstring("Download File", "Enter file hash to download:")
        if file_hash:
            async def _task_download():
                try:
                    logger.info(f"Attempting to download file with hash: {file_hash}")
                    success = await self.lantorrent_instance.download_file(file_hash.strip())
                    if success:
                        self.root.after(0, lambda: messagebox.showinfo("Download", f"Download started for hash: {file_hash}"))
                        self.root.after(0, self.update_status_display)
                    else:
                        self.root.after(0, lambda: messagebox.showerror("Download", f"Failed to start download for hash: {file_hash}. File may not be available, already downloading, or hash is incorrect. Check logs."))
                except Exception as e:
                    logger.error(f"Error during download task: {e}", exc_info=True)
                    self.root.after(0, lambda: messagebox.showerror("Download Error", f"An error occurred: {e}"))
            
            if self.async_loop.is_running():
                asyncio.run_coroutine_threadsafe(_task_download(), self.async_loop)
            else:
                messagebox.showerror("Error", "Async loop not running for download.")

    def on_closing(self):
        logger.info("Close button pressed. Shutting down...")
        if self.status_update_job:
            self.root.after_cancel(self.status_update_job)
            self.status_update_job = None
        
        if self.lantorrent_instance and self.lantorrent_instance.running and self.async_loop and self.async_loop.is_running():
            logger.info("Stopping LAN Torrent service...")
            future = asyncio.run_coroutine_threadsafe(self.lantorrent_instance.stop(), self.async_loop)
            try:
                future.result(timeout=10) # Wait for stop to complete
                logger.info("LANTorrent service stopped.")
            except TimeoutError:
                logger.warning("Timeout stopping LANTorrent service.")
            except Exception as e:
                logger.error(f"Error stopping LANTorrent: {e}", exc_info=True)
        
        if self.async_loop and self.async_loop.is_running():
            logger.info("Stopping asyncio event loop...")
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)
        
        if self.async_thread and self.async_thread.is_alive():
            logger.info("Joining asyncio thread...")
            self.async_thread.join(timeout=5)
            if self.async_thread.is_alive():
                logger.warning("Asyncio thread did not terminate cleanly.")
        
        logger.info("Destroying Tkinter root window.")
        self.root.destroy()

def main_gui_entry():
    # Setup basic logging if no handlers are configured by core app
    # This is useful if running the GUI directly
    if not logging.getLogger('lantorrent').handlers:
         logging.basicConfig(
            level=logging.INFO, # Or logging.DEBUG for more details
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)] # Ensure logs go to console
        )
    
    # Check if another instance is running using the CLI socket path logic (optional for GUI)
    # For simplicity, GUI will manage its own LANTorrent instance lifecycle.
    # If you want to connect to an existing service, that's a more complex feature.

    root = tk.Tk()
    app_ui = LANTorrentAppUI(root)
    root.protocol("WM_DELETE_WINDOW", app_ui.on_closing)
    root.mainloop()

if __name__ == '__main__':
    # This allows running the GUI directly for testing:
    # python lantorrent/gui/main.py
    # Ensure your project root (LANTorrent) is in PYTHONPATH or run from there.
    main_gui_entry()