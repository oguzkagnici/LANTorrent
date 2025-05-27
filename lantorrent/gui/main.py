import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import asyncio
import threading
from pathlib import Path
import logging
import sys
import os

# Adjust import path for core modules
try:
    from lantorrent.core.app import LANTorrent, handle_share_command
    from lantorrent.core.utils import format_size
except ImportError:
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from lantorrent.core.app import LANTorrent, handle_share_command
    from lantorrent.core.utils import format_size

logger = logging.getLogger('lantorrent.gui')


# Define the TextHandler class for redirecting logs to the GUI
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        self.setFormatter(self.formatter)

    def emit(self, record):
        msg = self.format(record)

        def append_message():
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(tk.END, msg + "\n")
            self.text_widget.config(state=tk.DISABLED)
            self.text_widget.see(tk.END)  # Scroll to the end

        if self.text_widget.winfo_exists():
            self.text_widget.after(0, append_message)


class DownloadSelectionDialog(simpledialog.Dialog):
    def __init__(self, parent, title, available_files_data):
        self.available_files_data = available_files_data
        self.result_hash = None
        self._file_display_map = {}
        super().__init__(parent, title)

    def body(self, master):
        master.pack_forget()
        dialog_frame = ttk.Frame(master, padding="10")
        dialog_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(dialog_frame, text="Select a file to download:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))

        display_options = []
        combobox_state = tk.DISABLED
        if not self.available_files_data:
            display_options.append("No files available from peers.")
        else:
            combobox_state = 'readonly'
            for f_info in self.available_files_data:
                display_str = f"{f_info['name']} ({f_info['formatted_size']}) - Peers: {f_info['peer_count']}"
                display_options.append(display_str)
                self._file_display_map[display_str] = f_info['hash']

        self.combobox = ttk.Combobox(dialog_frame, values=display_options, state=combobox_state, width=60)
        if display_options and self.available_files_data:
            self.combobox.current(0)
        self.combobox.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5, padx=5)
        dialog_frame.columnconfigure(0, weight=1)

        return self.combobox

    def buttonbox(self):
        box = ttk.Frame(self)

        ok_button_state = tk.NORMAL if self.available_files_data else tk.DISABLED

        self.ok_button = ttk.Button(box, text="OK", width=10, command=self.ok, default=tk.ACTIVE, state=ok_button_state)
        self.ok_button.pack(side=tk.LEFT, padx=5, pady=5)
        ttk.Button(box, text="Cancel", width=10, command=self.cancel).pack(side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok_pressed)
        self.bind("<Escape>", self.cancel)

        box.pack(pady=(0, 10))

    def ok_pressed(self, event=None):
        if self.ok_button['state'] != tk.DISABLED:
            self.ok()

    def apply(self):
        if not self.available_files_data:
            self.result_hash = None
            return

        selected_display_string = self.combobox.get()
        if selected_display_string in self._file_display_map:
            self.result_hash = self._file_display_map[selected_display_string]
        else:
            self.result_hash = None


class LANTorrentAppUI:
    def __init__(self, root_tk):
        self.root = root_tk
        self.root.title("LAN Torrent")
        self.root.geometry("800x750")  # Increased height for log area

        self.lantorrent_instance: LANTorrent | None = None
        self.async_loop = None
        self.async_thread = None
        self.app_logger = logging.getLogger('lantorrent')  # Initialize app_logger here

        if not self.app_logger.hasHandlers():  # Check app_logger specifically
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

        self.stop_button = ttk.Button(controls_frame, text="Stop Service", command=self.stop_service, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        self.share_button = ttk.Button(controls_frame, text="Share File", command=self.ui_share_file, state=tk.DISABLED)
        self.share_button.pack(side=tk.LEFT, padx=5)

        self.download_button = ttk.Button(controls_frame, text="Download File", command=self.ui_prompt_download_file, state=tk.DISABLED)
        self.download_button.pack(side=tk.LEFT, padx=5)

        display_frame = ttk.LabelFrame(main_frame, text="Status & Files", padding="10")
        display_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        main_frame.columnconfigure(0, weight=1)  # Ensure column expands

        self.status_text = tk.Text(display_frame, height=15, width=80, state=tk.DISABLED, wrap=tk.WORD)
        status_scrollbar = ttk.Scrollbar(display_frame, orient=tk.VERTICAL, command=self.status_text.yview)
        self.status_text.config(yscrollcommand=status_scrollbar.set)

        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Add Log Display Area ---
        logs_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        logs_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)

        self.log_text = tk.Text(logs_frame, height=10, width=80, state=tk.DISABLED, wrap=tk.WORD)
        log_scrollbar = ttk.Scrollbar(logs_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scrollbar.set)

        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main_frame.rowconfigure(1, weight=1)  # Weight for Status & Files frame
        main_frame.rowconfigure(2, weight=1)  # Weight for Logs frame, adjust as needed (e.g., weight=0 for fixed size based on height)

        # --- Setup Logging to GUI ---
        self.gui_log_handler = TextHandler(self.log_text)
        if self.gui_log_handler not in self.app_logger.handlers:  # Add if not already present
            self.app_logger.addHandler(self.gui_log_handler)

        # Set levels after ensuring handler is present
        if not self.app_logger.level or self.app_logger.level > logging.INFO:
            self.app_logger.setLevel(logging.INFO)
        self.gui_log_handler.setLevel(logging.INFO)

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
            share_dir = project_root / "shared"
            download_dir = project_root / "downloads"

            try:
                share_dir.mkdir(parents=True, exist_ok=True)
                download_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using share directory: {share_dir}")
                logger.info(f"Using download directory: {download_dir}")
            except OSError as e:
                messagebox.showerror("Directory Error", f"Could not create directories: {e}")
                return

            # Ensure GUI log handler is active before starting service operations
            if hasattr(self, 'gui_log_handler') and hasattr(self, 'app_logger'):
                if self.gui_log_handler not in self.app_logger.handlers:
                    self.app_logger.addHandler(self.gui_log_handler)
                    logger.info("GUI log handler reactivated.")

            self.lantorrent_instance = LANTorrent(share_dir=str(share_dir), download_dir=str(download_dir))

            self.async_loop = asyncio.new_event_loop()
            self.async_thread = threading.Thread(target=self._run_async_loop, args=(self.async_loop,), daemon=True)
            self.async_thread.start()

            asyncio.run_coroutine_threadsafe(self.lantorrent_instance.start(), self.async_loop)

            self.start_button.config(text="Service Running", state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)  # Enable Stop button
            self.share_button.config(state=tk.NORMAL)
            self.download_button.config(state=tk.NORMAL)
            messagebox.showinfo("Service", "LAN Torrent service started.")
            self.schedule_status_update()
        else:
            messagebox.showinfo("Service", "Service is already running.")

    def stop_service(self):
        logger.info("Stop Service button pressed.")
        if self.lantorrent_instance and self.lantorrent_instance.running:
            # Remove GUI log handler before intensive shutdown tasks
            if hasattr(self, 'gui_log_handler') and hasattr(self, 'app_logger'):
                if self.gui_log_handler in self.app_logger.handlers:
                    self.app_logger.removeHandler(self.gui_log_handler)
                    logging.getLogger('lantorrent.gui').info("GUI log handler temporarily deactivated for service stop.")

            self._perform_shutdown_tasks()

            # Reset UI elements
            self.start_button.config(text="Start Service", state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.share_button.config(state=tk.DISABLED)
            self.download_button.config(state=tk.DISABLED)

            # Clear the instance and related async resources to allow a clean restart
            self.lantorrent_instance = None
            self.async_loop = None
            self.async_thread = None

            self.status_text.config(state=tk.NORMAL)
            self.status_text.delete(1.0, tk.END)
            self.status_text.insert(tk.END, "Service stopped. Click 'Start Service' to begin.")
            self.status_text.config(state=tk.DISABLED)
            messagebox.showinfo("Service", "LAN Torrent service stopped.")
        else:
            messagebox.showinfo("Service", "Service is not currently running.")

    def _perform_shutdown_tasks(self):
        """Handles the actual stopping of the LANTorrent service and cleanup."""
        if self.status_update_job:
            self.root.after_cancel(self.status_update_job)
            self.status_update_job = None

        if self.lantorrent_instance and self.lantorrent_instance.running and self.async_loop and self.async_loop.is_running():
            logger.info("Stopping LAN Torrent service via _perform_shutdown_tasks...")
            future = asyncio.run_coroutine_threadsafe(self.lantorrent_instance.stop(), self.async_loop)
            try:
                future.result()
                logger.info("LANTorrent service stopped successfully.")
            except Exception as e:
                logger.error(f"Error during LANTorrent.stop(): {e}", exc_info=True)

        if self.async_loop and self.async_loop.is_running():
            logger.info("Stopping asyncio event loop...")
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)

        if self.async_thread and self.async_thread.is_alive():
            logger.info("Joining asyncio thread...")
            self.async_thread.join(timeout=5)
            if self.async_thread.is_alive():
                logger.warning("Asyncio thread did not terminate cleanly.")
            else:
                logger.info("Asyncio thread joined successfully.")

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

                all_peer_files_display_info = {}
                if status_data.get('peers') and self.lantorrent_instance:
                    files_and_their_peers = {}
                    for peer_id, peer_obj in self.lantorrent_instance.peer_manager.peers.items():
                        for f_hash, file_details_from_peer in peer_obj.files.items():
                            if not isinstance(file_details_from_peer, dict) or \
                               'name' not in file_details_from_peer or \
                               'size' not in file_details_from_peer:
                                logger.warning(f"Peer {peer_id} has malformed file data for hash {f_hash}: {file_details_from_peer}")
                                continue

                            if f_hash not in status_data.get('shared_files', {}):
                                if f_hash not in files_and_their_peers:
                                    files_and_their_peers[f_hash] = {
                                        'name': file_details_from_peer['name'],
                                        'size': file_details_from_peer['size'],
                                        'peers': set()
                                    }
                                files_and_their_peers[f_hash]['peers'].add(peer_id)

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
                        total_size = fdata.get('size', 0)
                        progress_fraction = fdata.get('progress', 0)
                        bytes_downloaded = int(progress_fraction * total_size)
                        progress_percentage = progress_fraction * 100
                        
                        bar_length = 20  # Length of the text progress bar
                        filled_length = int(bar_length * progress_fraction)
                        bar = '█' * filled_length + '-' * (bar_length - filled_length)

                        display_content += f"  - {fdata.get('name', 'N/A')} ({format_size(total_size)})\n"
                        display_content += f"    Hash: {fhash}\n"
                        display_content += f"    Progress: [{bar}] {progress_percentage:.2f}% ({format_size(bytes_downloaded)} / {format_size(total_size)})\n"
                        
                        # Display bytes downloaded from each peer, if data is available
                        peer_contributions = fdata.get('peer_contributions') # You'll need to add this to get_status() in core/app.py
                        if peer_contributions:
                            display_content += f"    Sources:\n"
                            if peer_contributions: # Check if the dictionary is not empty
                                for peer_short_id, contrib_data in peer_contributions.items():
                                    bytes_from_peer = contrib_data.get('bytes_downloaded', 0)
                                    display_content += f"      - Peer {peer_short_id}: {format_size(bytes_from_peer)}\n"
                            else:
                                display_content += f"      (No specific peer source data for this file yet)\n"
                        # Add a newline after each downloading file entry for better separation
                        display_content += "\n" 
                else:
                    display_content += "  No files currently downloading.\n"
                display_content += "\n" # Extra newline after the "Downloading Files" section

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

    def _get_downloadable_files_list(self):
        downloadable_files = []
        if self.lantorrent_instance and self.lantorrent_instance.peer_manager:
            status_data = self.lantorrent_instance.get_status()
            my_shared_hashes = set(status_data.get('shared_files', {}).keys())

            files_and_their_peers = {}
            for peer_id, peer_obj in self.lantorrent_instance.peer_manager.peers.items():
                if peer_id == self.lantorrent_instance.peer_manager.my_id:
                    continue
                for f_hash, file_details_from_peer in peer_obj.files.items():
                    if not isinstance(file_details_from_peer, dict) or \
                       'name' not in file_details_from_peer or \
                       'size' not in file_details_from_peer:
                        logger.warning(f"Peer {peer_id} has malformed file data for hash {f_hash}: {file_details_from_peer}")
                        continue

                    if f_hash not in my_shared_hashes:
                        if f_hash not in files_and_their_peers:
                            files_and_their_peers[f_hash] = {
                                'name': file_details_from_peer['name'],
                                'size': file_details_from_peer['size'],
                                'peers': set()
                            }
                        files_and_their_peers[f_hash]['peers'].add(peer_id)

            for f_hash, data in files_and_their_peers.items():
                if data['peers']:
                    downloadable_files.append({
                        'hash': f_hash,
                        'name': data['name'],
                        'size': data['size'],
                        'formatted_size': format_size(data['size']),
                        'peer_count': len(data['peers'])
                    })

        downloadable_files.sort(key=lambda x: x['name'].lower())
        return downloadable_files

    def ui_share_file(self):
        if not self.lantorrent_instance or not self.lantorrent_instance.running or not self.async_loop:
            messagebox.showerror("Error", "Service not running. Please start the service first.")
            return

        filepath = filedialog.askopenfilename(title="Select file to share")
        if filepath:
            async def _task_share():
                try:
                    logger.info(f"Attempting to share file: {filepath} via handle_share_command")

                    args_for_command = {'file': filepath}
                    result_dict = await handle_share_command(self.lantorrent_instance, args_for_command)

                    if result_dict.get('success'):
                        success_message = result_dict.get('output', f"File '{Path(filepath).name}' shared successfully.")
                        self.root.after(0, lambda: messagebox.showinfo("Share", success_message))
                        self.root.after(0, self.update_status_display)
                    else:
                        error_message = result_dict.get('error', f"Failed to share file '{Path(filepath).name}'. Check logs.")
                        self.root.after(0, lambda: messagebox.showerror("Share", error_message))
                except Exception as e:
                    logger.error(f"Error during share task (using handle_share_command): {e}", exc_info=True)
                    self.root.after(0, lambda: messagebox.showerror("Share Error", f"An unexpected error occurred: {e}"))

            if self.async_loop.is_running():
                asyncio.run_coroutine_threadsafe(_task_share(), self.async_loop)
            else:
                messagebox.showerror("Error", "Async loop not running for sharing.")

    def ui_prompt_download_file(self):
        if not self.lantorrent_instance or not self.lantorrent_instance.running or not self.async_loop:
            messagebox.showerror("Error", "Service not running. Please start the service first.")
            return

        available_files_list = self._get_downloadable_files_list()

        dialog = DownloadSelectionDialog(self.root, "Download File", available_files_list)
        file_hash_to_download = dialog.result_hash

        if file_hash_to_download:
            async def _task_download():
                try:
                    logger.info(f"Attempting to download file with hash: {file_hash_to_download}")
                    success = await self.lantorrent_instance.download_file(file_hash_to_download)
                    if success:
                        self.root.after(0, lambda: messagebox.showinfo("Download", f"Download started for hash: {file_hash_to_download}"))
                        self.root.after(0, self.update_status_display)
                    else:
                        self.root.after(0, lambda: messagebox.showerror("Download", f"Failed to start download for hash: {file_hash_to_download}. File may not be available, already downloading, or hash is incorrect. Check logs."))
                except Exception as e:
                    logger.error(f"Error during download task: {e}", exc_info=True)
                    error_message = str(e)
                    self.root.after(0, lambda: messagebox.showerror("Download Error", f"An error occurred: {error_message}"))

            if self.async_loop.is_running():
                asyncio.run_coroutine_threadsafe(_task_download(), self.async_loop)
            else:
                messagebox.showerror("Error", "Async loop not running for download.")
        elif dialog.winfo_exists() and not available_files_list:
            pass
        elif not file_hash_to_download and available_files_list:
            logger.info("Download dialog cancelled or selection failed.")

    def on_closing(self):
        logger.info("Close button pressed. Shutting down...")

        # Remove the GUI log handler first
        if hasattr(self, 'gui_log_handler') and hasattr(self, 'app_logger'):
            if self.gui_log_handler in self.app_logger.handlers:
                self.app_logger.removeHandler(self.gui_log_handler)
                logging.getLogger('lantorrent.gui').info("GUI log handler removed for application shutdown.")

        self._perform_shutdown_tasks()

        logger.info("Destroying Tkinter root window.")
        self.root.destroy()


def main_gui_entry():
    if not logging.getLogger('lantorrent').handlers:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )

    root = tk.Tk()
    app_ui = LANTorrentAppUI(root)
    root.protocol("WM_DELETE_WINDOW", app_ui.on_closing)
    root.mainloop()


if __name__ == '__main__':
    main_gui_entry()