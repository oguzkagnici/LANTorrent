import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import asyncio
import threading
from pathlib import Path
import logging
import sys

# Import core modules, adjust path if running directly
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

class TextHandler(logging.Handler):
    """Redirect log records to a Tkinter Text widget."""
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
            self.text_widget.see(tk.END)
        if self.text_widget.winfo_exists():
            self.text_widget.after(0, append_message)

class DownloadSelectionDialog(simpledialog.Dialog):
    """Dialog for selecting a file to download from available peer files."""
    def __init__(self, parent, title, available_files_data):
        self.available_files_data = available_files_data
        self.result_hash = None
        self._file_display_map = {}
        self.auto_share_var = tk.BooleanVar(value=True)
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

        self.auto_share_checkbox = ttk.Checkbutton(
            dialog_frame, text="Automatically share after download", variable=self.auto_share_var
        )
        self.auto_share_checkbox.grid(row=2, column=0, sticky=tk.W, pady=(5,0), padx=5)
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
    """Main class for the LAN Torrent Tkinter application."""
    def __init__(self, root_tk):
        self.root = root_tk
        self.root.title("LAN Torrent")
        self.root.geometry("1200x800")  # Window size

        self.lantorrent_instance: LANTorrent | None = None
        self.async_loop = None
        self.async_thread = None
        self.app_logger = logging.getLogger('lantorrent')

        if not self.app_logger.hasHandlers():
            logging.basicConfig(level=logging.INFO,
                                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        self.create_widgets()
        self.status_update_job = None

    def create_widgets(self):
        # Main layout frames
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # Controls (top)
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

        # Status & Files (left)
        display_frame = ttk.LabelFrame(main_frame, text="Status & Files", padding="10")
        display_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5, padx=(0, 5))
        display_frame.rowconfigure(0, weight=1)
        display_frame.rowconfigure(1, weight=0)
        display_frame.columnconfigure(0, weight=1)
        display_frame.columnconfigure(1, weight=0)

        # Scrollable status area
        self.status_canvas = tk.Canvas(display_frame, borderwidth=0)
        self.status_canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        status_scrollbar = ttk.Scrollbar(display_frame, orient=tk.VERTICAL, command=self.status_canvas.yview)
        status_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.status_canvas.configure(yscrollcommand=status_scrollbar.set)
        self.status_content_frame = ttk.Frame(self.status_canvas, padding="5")
        self.status_canvas_window = self.status_canvas.create_window((0, 0), window=self.status_content_frame, anchor="nw")
        def _configure_canvas_scrollregion(event):
            self.status_canvas.configure(scrollregion=self.status_canvas.bbox("all"))
        def _configure_canvas_width(event):
            self.status_canvas.itemconfig(self.status_canvas_window, width=event.width)
        self.status_content_frame.bind("<Configure>", _configure_canvas_scrollregion)
        self.status_canvas.bind("<Configure>", _configure_canvas_width)

        # Current Downloads (below status)
        self.current_downloads_outer_frame = ttk.LabelFrame(display_frame, text="Current Downloads", padding="5")
        self.current_downloads_outer_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.S), pady=(10,0))
        self.downloads_list_frame = ttk.Frame(self.current_downloads_outer_frame)
        self.downloads_list_frame.pack(fill=tk.X, expand=True)

        # Logs (right)
        logs_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        logs_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5, padx=(5, 0))
        logs_frame.rowconfigure(0, weight=1)
        logs_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(logs_frame, height=10, width=50, state=tk.DISABLED, wrap=tk.WORD)
        log_scrollbar = ttk.Scrollbar(logs_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.gui_log_handler = TextHandler(self.log_text)
        if self.gui_log_handler not in self.app_logger.handlers:
            self.app_logger.addHandler(self.gui_log_handler)
        if not self.app_logger.level or self.app_logger.level > logging.INFO:
            self.app_logger.setLevel(logging.INFO)
        self.gui_log_handler.setLevel(logging.INFO)

    def _run_async_loop(self, loop_to_run):
        asyncio.set_event_loop(loop_to_run)
        try:
            loop_to_run.run_forever()
        finally:
            loop_to_run.close()

    def start_service(self):
        # Start the LANTorrent core service and event loop
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
            self.stop_button.config(state=tk.NORMAL)
            self.share_button.config(state=tk.NORMAL)
            self.download_button.config(state=tk.NORMAL)
            messagebox.showinfo("Service", "LAN Torrent service started.")
            self.schedule_status_update()
        else:
            messagebox.showinfo("Service", "Service is already running.")

    def stop_service(self):
        logger.info("Stop Service button pressed.")
        if self.lantorrent_instance and self.lantorrent_instance.running:
            if hasattr(self, 'gui_log_handler') and hasattr(self, 'app_logger'):
                if self.gui_log_handler in self.app_logger.handlers:
                    self.app_logger.removeHandler(self.gui_log_handler)
                    logging.getLogger('lantorrent.gui').info("GUI log handler temporarily deactivated for service stop.")
            self._perform_shutdown_tasks()
            self.start_button.config(text="Start Service", state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.share_button.config(state=tk.DISABLED)
            self.download_button.config(state=tk.DISABLED)
            self.lantorrent_instance = None
            self.async_loop = None
            self.async_thread = None
            self.update_status_display()
            messagebox.showinfo("Service", "LAN Torrent service stopped.")
        else:
            messagebox.showinfo("Service", "Service is not currently running.")

    def _perform_shutdown_tasks(self):
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
        # Schedule periodic status updates
        if self.lantorrent_instance and self.lantorrent_instance.running:
            self.update_status_display()
            self.status_update_job = self.root.after(1000, self.schedule_status_update)
        elif not self.lantorrent_instance and self.status_update_job:
            self.root.after_cancel(self.status_update_job)
            self.status_update_job = None
            self.update_status_display()

    def update_status_display(self):
        # Update scrollable status content
        for widget in self.status_content_frame.winfo_children():
            widget.destroy()
        if not self.lantorrent_instance or not self.lantorrent_instance.running:
            msg_label = ttk.Label(self.status_content_frame, text="Service not running. Click 'Start Service' to begin.", font=("Arial", 10))
            msg_label.pack(pady=20, padx=10, anchor="center")
            self.status_content_frame.update_idletasks()
            self._update_current_downloads_display({})
            return
        try:
            status_data = self.lantorrent_instance.get_status()
            general_frame = ttk.LabelFrame(self.status_content_frame, text="My Info", padding="5")
            general_frame.pack(fill=tk.X, expand=True, pady=(0,5), padx=5)
            ttk.Label(general_frame, text=f"My ID: {status_data.get('id', 'N/A')}").pack(anchor="w", padx=5, pady=2)
            ttk.Label(general_frame, text=f"Address: {status_data.get('address', 'N/A')}").pack(anchor="w", padx=5, pady=2)
            self._display_peers_info(status_data.get('peers', {}))
            self._display_shared_files_info(status_data.get('shared_files', {}))
            downloadable_files_list = self._get_downloadable_files_list()
            self._display_downloadable_files_info(downloadable_files_list)
            self._display_downloaded_files_info(status_data.get('downloaded', {}))
            self._update_current_downloads_display(status_data.get('downloading', {}))
        except Exception as e:
            logger.error(f"Error updating status display: {e}", exc_info=True)
            ttk.Label(self.status_content_frame, text=f"Error updating status: {e}").pack(pady=10, padx=10, anchor="w")
            self._update_current_downloads_display({})
        self.status_content_frame.update_idletasks()

    def _update_current_downloads_display(self, downloading_data):
        # Show current downloads with progress bars
        for widget in self.downloads_list_frame.winfo_children():
            widget.destroy()
        if not downloading_data:
            ttk.Label(self.downloads_list_frame, text="No active downloads.").pack(pady=5, padx=5, anchor="w")
            return
        for f_hash, fdata in downloading_data.items():
            item_frame = ttk.Frame(self.downloads_list_frame, padding=(0, 3))
            item_frame.pack(fill=tk.X, expand=True, pady=(0,2))
            name = fdata.get('name', 'N/A')
            size = fdata.get('size', 0)
            progress_fraction = fdata.get('progress', 0)
            progress_percentage = progress_fraction * 100
            info_text = f"{name} ({format_size(size)})"
            content_item_frame = ttk.Frame(item_frame)
            content_item_frame.pack(fill=tk.X, expand=True)
            label = ttk.Label(content_item_frame, text=info_text, anchor="w")
            label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,10))
            pb = ttk.Progressbar(content_item_frame, orient=tk.HORIZONTAL, length=150, mode='determinate', value=progress_percentage)
            pb.pack(side=tk.LEFT, padx=(0,5))
            percent_label = ttk.Label(content_item_frame, text=f"{progress_percentage:.1f}%", width=6, anchor="e")
            percent_label.pack(side=tk.LEFT, padx=(5,0))

    def _create_treeview_frame(self, parent_frame, title_text, columns_config, data_list, empty_message_values):
        # Helper for displaying tabular data
        frame = ttk.LabelFrame(parent_frame, text=title_text, padding="5")
        frame.pack(fill=tk.X, expand=True, pady=5, padx=5)
        tree_container = ttk.Frame(frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(tree_container, columns=list(columns_config.keys()), show="headings")
        col_ids = list(columns_config.keys())
        for col_id in col_ids:
            heading_text, width, anchor, stretch = columns_config[col_id]
            tree.heading(col_id, text=heading_text, anchor=tk.W)
            tree.column(col_id, width=width, anchor=anchor, minwidth=max(40, width//2), stretch=stretch)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=scrollbar_x.set)
        scrollbar_x.pack(fill=tk.X, pady=(2,0))
        if data_list:
            for item_values in data_list:
                tree.insert("", tk.END, values=item_values)
        else:
            tree.insert("", tk.END, values=empty_message_values, tags=('empty_row',))
            tree.tag_configure('empty_row', foreground='grey')
        return tree

    def _display_peers_info(self, peers_data):
        columns = {
            "id": ("ID", 70, tk.W, False),
            "address": ("Address", 130, tk.W, False),
            "files": ("Files", 40, tk.CENTER, False),
            "up": ("Up", 70, tk.E, False),
            "down": ("Down", 70, tk.E, False)
        }
        data = []
        if peers_data:
            for pid, pdata in peers_data.items():
                data.append((
                    pid[:8] + "...",
                    f"{pdata['ip']}:{pdata['port']}",
                    pdata.get('files', 0),
                    format_size(pdata.get('upload', 0)),
                    format_size(pdata.get('download', 0))
                ))
        self._create_treeview_frame(self.status_content_frame, "Peers", columns, data, 
                                    ("No peers connected.", "", "", "", ""))

    def _display_shared_files_info(self, shared_files_data):
        columns = {
            "name": ("Name", 200, tk.W, True),
            "size": ("Size", 80, tk.E, False),
            "hash": ("Hash", 250, tk.W, True)
        }
        data = []
        if shared_files_data:
            for fhash, fdata in shared_files_data.items():
                data.append((
                    fdata['name'],
                    format_size(fdata['size']),
                    fhash
                ))
        self._create_treeview_frame(self.status_content_frame, "My Shared Files", columns, data,
                                    ("Not sharing any files yet.", "", ""))

    def _display_downloadable_files_info(self, downloadable_files_list):
        columns = {
            "name": ("Name", 200, tk.W, True),
            "size": ("Size", 80, tk.E, False),
            "peers": ("Peers", 50, tk.CENTER, False),
            "hash": ("Hash", 250, tk.W, True)
        }
        data = []
        if downloadable_files_list:
            for file_info in downloadable_files_list:
                data.append((
                    file_info['name'],
                    file_info['formatted_size'],
                    file_info['peer_count'],
                    file_info['hash']
                ))
        self._create_treeview_frame(self.status_content_frame, "Downloadable Files (from peers)", columns, data,
                                    ("No files discovered from peers yet.", "", "", ""))

    def _display_downloaded_files_info(self, downloaded_files_data):
        columns = {
            "name": ("Name", 180, tk.W, True),
            "size": ("Size", 80, tk.E, False),
            "completed_at": ("Completed At", 130, tk.W, False),
            "sources": ("Sources", 120, tk.W, True),
            "hash": ("Hash", 150, tk.W, True)
        }
        data = []
        if downloaded_files_data:
            for fhash, fdata in downloaded_files_data.items():
                peer_contributions = fdata.get('peer_contributions')
                sources_str = "N/A"
                if peer_contributions:
                    sources_list = [f"{psid[:6]}..: {format_size(cdata.get('bytes_downloaded',0))}" 
                                    for psid, cdata in peer_contributions.items()]
                    if sources_list:
                        sources_str = ", ".join(sources_list)
                data.append((
                    fdata['name'],
                    format_size(fdata['size']),
                    fdata['downloaded_at'],
                    sources_str,
                    fhash
                ))
        self._create_treeview_frame(self.status_content_frame, "Completed Downloads", columns, data,
                                    ("No files downloaded yet.", "", "", "", ""))

    def _get_downloadable_files_list(self):
        # Aggregate files available from peers, excluding already shared/downloaded
        downloadable_files = []
        if self.lantorrent_instance and self.lantorrent_instance.peer_manager:
            status_data = self.lantorrent_instance.get_status()
            my_shared_hashes = set(status_data.get('shared_files', {}).keys())
            my_downloaded_hashes = set(status_data.get('downloaded', {}).keys())
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
                    if f_hash not in my_shared_hashes and f_hash not in my_downloaded_hashes:
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
        # Share a file via file dialog and core handler
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
        # Download a file selected from the dialog
        if not self.lantorrent_instance or not self.lantorrent_instance.running or not self.async_loop:
            messagebox.showerror("Error", "Service not running. Please start the service first.")
            return
        available_files_list = self._get_downloadable_files_list()
        dialog = DownloadSelectionDialog(self.root, "Download File", available_files_list)
        file_hash_to_download = dialog.result_hash
        auto_share_after_download = dialog.auto_share_var.get()
        if file_hash_to_download:
            async def _task_download():
                try:
                    logger.info(f"Attempting to download file with hash: {file_hash_to_download}, auto_share: {auto_share_after_download}")
                    success = await self.lantorrent_instance.download_file(file_hash_to_download, auto_share=auto_share_after_download)
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
        # Handle window close event
        logger.info("Close button pressed. Shutting down...")
        if hasattr(self, 'gui_log_handler') and hasattr(self, 'app_logger'):
            if self.gui_log_handler in self.app_logger.handlers:
                self.app_logger.removeHandler(self.gui_log_handler)
                logging.getLogger('lantorrent.gui').info("GUI log handler removed for application shutdown.")
        self._perform_shutdown_tasks()
        logger.info("Destroying Tkinter root window.")
        self.root.destroy()

def main_gui_entry():
    # Entry point for running the GUI
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