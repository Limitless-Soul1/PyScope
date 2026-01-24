"""📦 PyPI Manager - Simplified UI Module"""

import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
import time
import re
import threading
import os
import sys
from collections import deque
from PIL import Image, ImageTk
from utils import COLORS, logger


def safe_ui_callback(widget, callback, *args, **kwargs):
    """Execute UI callback safely in main thread."""
    if widget and hasattr(widget, 'after'):
        widget.after_idle(lambda: callback(*args, **kwargs) if widget.winfo_exists() else None)


def set_window_icon(window, parent=None):
    """Set window icon reliably for any window."""
    def _apply_icon():
        try:
            icon_path = None
            if hasattr(parent, 'icon_path') and parent.icon_path:
                icon_path = parent.icon_path
            elif hasattr(window, 'master') and hasattr(window.master, 'icon_path'):
                icon_path = window.master.icon_path

            if icon_path and os.path.exists(icon_path) and icon_path.endswith('.ico'):
                try:
                    window.iconbitmap(icon_path)
                    logger.debug(f"Applied iconbitmap: {icon_path}")
                    return
                except Exception as e:
                    logger.debug(f"Failed to apply iconbitmap: {e}")

            if icon_path and os.path.exists(icon_path):
                try:
                    img = Image.open(icon_path)
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                        
                    photo = ImageTk.PhotoImage(img)
                    window.iconphoto(False, photo)
                    window._icon_photo = photo
                    logger.debug(f"Applied iconphoto from PIL: {icon_path}")
                    return
                except Exception as e:
                    logger.debug(f"Failed to apply iconphoto from PIL: {e}")

            icon_img = None
            if hasattr(parent, 'window_icon') and parent.window_icon:
                icon_img = parent.window_icon
            elif hasattr(window, 'master') and hasattr(window.master, 'window_icon'):
                icon_img = window.master.window_icon

            if icon_img:
                try:
                    window.iconphoto(False, icon_img)
                    logger.debug("Applied iconphoto from object")
                    return
                except Exception as e:
                    logger.debug(f"Failed to apply iconphoto: {e}")

        except Exception as e:
            logger.error(f"Error in set_window_icon: {e}")

    window.after(200, _apply_icon)


class EntryContextMenu:
    """Context menu for text entries with clipboard support."""
    
    MAX_LENGTH = 255
    
    @staticmethod
    def bind_to_entry(entry):
        """Bind context menu to entry widget."""
        widget = entry._entry if hasattr(entry, '_entry') else entry
        menu = tk.Menu(entry, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"])
        
        for label, cmd, acc in [
            ("Cut", '<<Cut>>', "Ctrl+X"),
            ("Copy", '<<Copy>>', "Ctrl+C"),
            ("Paste", lambda: EntryContextMenu._paste_safe(widget, entry), "Ctrl+V"),
            ("Select All", lambda: EntryContextMenu._select_all(widget, entry), "Ctrl+A")
        ]:
            menu.add_command(label=label, command=cmd if isinstance(cmd, str) else cmd, accelerator=acc)
        
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        
        shortcuts = {
            "<Control-v>": lambda e: EntryContextMenu._paste_safe(widget, entry) or "break",
            "<Control-c>": lambda e: widget.event_generate('<<Copy>>') or "break",
            "<Control-x>": lambda e: widget.event_generate('<<Cut>>') or "break",
            "<Control-a>": lambda e: EntryContextMenu._select_all(widget, entry) or "break"
        }
        
        for key, handler in shortcuts.items():
            widget.bind(key, handler)
        
        return menu
    
    @staticmethod
    def _paste_safe(widget, ctk_widget):
        """Safely paste content with validation."""
        try:
            content = widget.clipboard_get()
            if not content:
                return
            content = re.sub(r'[^a-zA-Z0-9\s._\-@=<>~!]', '', content)[:255]
            
            try:
                start, end = widget.index(tk.SEL_FIRST), widget.index(tk.SEL_LAST)
                ctk_widget.delete(start, end)
                ctk_widget.insert(start, content)
            except Exception:
                ctk_widget.insert(tk.INSERT, content)
        except Exception:
            try:
                widget.event_generate('<<Paste>>')
            except Exception:
                pass
    
    @staticmethod
    def _select_all(widget, ctk_widget):
        """Select all text in widget."""
        try:
            text = ctk_widget.get() if ctk_widget else widget.get()
            if len(text) > 1000:
                widget.delete(0, tk.END)
                widget.insert(0, "[Text too long - cleared]")
                widget.after(2000, lambda: widget.delete(0, tk.END))
                return
            widget.select_range(0, tk.END)
            widget.focus_force()
        except Exception:
            pass


class ProgressDialog(ctk.CTkToplevel):
    """Progress dialog for package installation/update operations."""
    
    def __init__(self, parent, package_name, version=None, action="install", environment_info=None):
        super().__init__(parent)
        
        set_window_icon(self, parent)
        
        self.package_name = package_name
        self.action = action
        self.version = version
        self.environment_info = environment_info
        self._is_closing = False
        self.progress = 0
        self.phase = "preparing"
        self.logs = deque(maxlen=100)
        self.start_time = time.time()
        
        self._setup_window(parent)
        self._build_ui()
        self._init_state()
    
    def _setup_window(self, parent):
        """Configure window properties."""
        env_display = ""
        if self.environment_info:
            env_name = self.environment_info.get("display", "").split("(")[0].strip()
            env_display = f" [{env_name}]"
        
        self.title(f"📦 {self.action.title()} {self.package_name}{env_display}")
        self.configure(fg_color=COLORS["bg"])
        self.transient(parent)
        self.geometry("700x600")
        self.resizable(False, False)
        
        # Center window on parent
        try:
            parent_x = parent.winfo_x()
            parent_y = parent.winfo_y()
            parent_w = parent.winfo_width() or 800
            parent_h = parent.winfo_height() or 600
            
            win_w = 700
            win_h = 600
            x = parent_x + (parent_w - win_w) // 2
            y = parent_y + (parent_h - win_h) // 2
            
            screen_width = parent.winfo_screenwidth()
            screen_height = parent.winfo_screenheight()
            
            x = max(0, min(x, screen_width - win_w))
            y = max(0, min(y, screen_height - win_h))
            
            self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception as e:
            self.geometry("700x600")
            logger.debug(f"Error calculating progress dialog position: {e}")
        
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())
    
    def _build_ui(self):
        """Build progress dialog UI."""
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        main.pack(fill="both", expand=True, padx=1, pady=1)
        
        # Header
        header = ctk.CTkFrame(main, fg_color=COLORS["surface"], height=70)
        header.pack(fill="x", padx=12, pady=12)
        
        self.icon = ctk.CTkLabel(header, text="⏳", font=("Segoe UI Emoji", 24))
        self.icon.pack(side="left", padx=15)
        
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left", fill="y")
        
        self.title_label = ctk.CTkLabel(title_frame, text=self.package_name, 
                                       font=("Segoe UI", 16, "bold"))
        self.title_label.pack(anchor="w")
        
        version_text = f"v{self.version}" if self.version else "latest"
        self.subtitle = ctk.CTkLabel(title_frame, 
                                    text=f"{self.action.title()} {version_text}",
                                    font=("Segoe UI", 11))
        self.subtitle.pack(anchor="w")
        
        # Environment info
        if self.environment_info:
            env_text = self.environment_info.get("display", "").split("(")[0].strip()
            self.env_label = ctk.CTkLabel(title_frame, 
                                         text=f"Environment: {env_text}",
                                         font=("Segoe UI", 9),
                                         text_color=COLORS["subtext"])
            self.env_label.pack(anchor="w")
        
        self.phase_label = ctk.CTkLabel(header, text="Preparing...", font=("Segoe UI", 14, "bold"))
        self.phase_label.pack(side="right", padx=15)
        
        # Progress section
        progress_frame = ctk.CTkFrame(main, fg_color=COLORS["surface"])
        progress_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        self.status = ctk.CTkLabel(progress_frame, text="", 
                                  font=("Segoe UI", 13, "bold"))
        self.status.pack(anchor="w", padx=20, pady=(20, 10))
        
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            mode='indeterminate', 
            length=650,
            style="Moving.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 15))
        
        # Details
        details = ctk.CTkFrame(progress_frame, fg_color="transparent")
        details.pack(fill="x", padx=20, pady=(0, 20))
        
        self.size_label = ctk.CTkLabel(details, text="")
        self.size_label.pack(side="left")
        
        self.time_label = ctk.CTkLabel(details, text="Elapsed: 0s")
        self.time_label.pack(side="right")
        
        # Log output area
        log_frame = ctk.CTkFrame(main, fg_color=COLORS["surface"], height=150)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        log_frame.pack_propagate(False)
        
        self.log_text = tk.Text(log_frame, bg=COLORS["card"], fg=COLORS["text"], 
                               font=("Consolas", 9), wrap="word", height=8)
        self.log_text.pack(fill="both", expand=True, padx=1, pady=1)
        self.log_text.config(state="disabled")
        
        # Action buttons
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        self.cancel_btn = ctk.CTkButton(btn_frame, text="Cancel", width=100,
                                       fg_color=COLORS["danger"], command=self.destroy)
        self.cancel_btn.pack(side="left")
        
        self.copy_log_btn = ctk.CTkButton(btn_frame, text="Copy Log", width=80,
                                         fg_color=COLORS["surface"], command=self._copy_log)
        self.copy_log_btn.pack(side="right")
    
    def _init_state(self):
        """Initialize dialog state."""
        self.after(50, self.deiconify)
        self._update_timer()
        self.progress_bar.start(20)
    
    def _update_timer(self):
        """Update elapsed time display."""
        if self._is_closing or not self.winfo_exists():
            return
        elapsed = int(time.time() - self.start_time)
        self.time_label.configure(text=f"Elapsed: {elapsed}s")
        self.after(1000, self._update_timer)
    
    def _update_icon(self):
        """Update phase icon."""
        icons = {"preparing": "⏳", "downloading": "⬇️", "installing": "⚙️", 
                "completed": "✅", "failed": "❌"}
        self.icon.configure(text=icons.get(self.phase, "⏳"))
        self.phase_label.configure(text=self.phase.title())
    
    def _add_log(self, text):
        """Add text to log area."""
        if not self.winfo_exists():
            return
        
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
    
    def _copy_log(self):
        """Copy log to clipboard."""
        try:
            content = self.log_text.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(content)
            self.copy_log_btn.configure(text="Copied!", fg_color=COLORS["success"])
            self.after(2000, lambda: self.copy_log_btn.configure(text="Copy Log", fg_color=COLORS["surface"]))
        except Exception:
            pass
    
    def update_progress(self, data):
        """Update progress display with data."""
        if self._is_closing:
            return
        
        def update():
            dtype = data.get('type', 'output')
            
            if dtype == 'progress':
                if downloaded := data.get('downloaded', 0):
                    self.size_label.configure(text=f"Downloaded: {downloaded/1024:.1f} MB")
                
                self.phase = "downloading"
                self.status.configure(text="Downloading...")
            
            elif dtype == 'success':
                self.phase = "completed"
                self.status.configure(text="Installation completed!")
                self.cancel_btn.configure(text="Close", fg_color=COLORS["success"])
                self.progress_bar.stop()
            
            elif dtype == 'error':
                self.phase = "failed"
                error_msg = data.get('message', 'Unknown')[:50]
                self.status.configure(text=f"Error: {error_msg}...")
                self.progress_bar.stop()
            
            elif dtype == 'collecting':
                self.phase = "downloading"
                self.status.configure(text="Collecting package information...")
            
            elif dtype == 'installing':
                self.phase = "installing"
                self.status.configure(text="Installing package...")
            
            if 'line' in data:
                self._add_log(data['line'])
            
            self._update_icon()
        
        safe_ui_callback(self, update)
    
    def set_completed(self, success=True, message=""):
        """Mark operation as completed."""
        if success:
            self.update_progress({'type': 'success'})
            self._add_log(f"✅ {message}")
        else:
            self.update_progress({'type': 'error', 'message': message})
            self._add_log(f"❌ {message}")


class SearchDialog(ctk.CTkToplevel):
    """Dialog for searching and installing new packages."""
    
    def __init__(self, parent, core, on_close=None, current_environment=None):
        super().__init__(parent)
        
        set_window_icon(self, parent)
        
        self.core = core
        self.on_close = on_close
        self.current_environment = current_environment
        self._is_closing = False
        self.results = []
        
        self._setup_window(parent)
        self._build_ui()
    
    def _setup_window(self, parent):
        """Configure window properties."""
        self.title("Install New Packages")
        self.configure(fg_color=COLORS["bg"])
        self.transient(parent)
        self.minsize(900, 650)
        
        if self.current_environment:
            env_name = self.current_environment.get("display", "").split("(")[0].strip()
            self.title(f"Install New Packages [{env_name}]")
        
        # Center window on parent
        try:
            parent_x = parent.winfo_x()
            parent_y = parent.winfo_y()
            parent_w = parent.winfo_width() or 1100
            parent_h = parent.winfo_height() or 700
            
            win_w = 900
            win_h = 650
            x = parent_x + (parent_w - win_w) // 2
            y = parent_y + (parent_h - win_h) // 2
            
            screen_width = parent.winfo_screenwidth()
            screen_height = parent.winfo_screenheight()
            
            x = max(0, min(x, screen_width - win_w))
            y = max(0, min(y, screen_height - win_h))
            
            self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception as e:
            self.geometry("900x650")
            logger.debug(f"Error calculating search dialog position: {e}")
        
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
    
    def _build_ui(self):
        """Build search dialog UI."""
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        main.pack(fill="both", expand=True, padx=1, pady=1)
        
        # Search bar
        search_frame = ctk.CTkFrame(main, fg_color="transparent")
        search_frame.pack(fill="x", padx=20, pady=15)
        
        self.entry = ctk.CTkEntry(search_frame, placeholder_text="Type library name",
                                 width=400, height=32, font=("Segoe UI", 12))
        self.entry.pack(side="left")
        EntryContextMenu.bind_to_entry(self.entry)
        
        self.search_btn = ctk.CTkButton(search_frame, text="Search", width=80,
                                       command=self._safe_search, state="disabled",
                                       font=("Segoe UI", 11, "bold"))
        self.search_btn.pack(side="left", padx=10)
        
        ctk.CTkButton(search_frame, text="Clear", width=60,
                     command=self.clear,
                     font=("Segoe UI", 11, "bold")).pack(side="left")
        
        self.entry.bind("<KeyRelease>", lambda e: self._update_search_btn())
        self.entry.bind("<Return>", lambda e: self._safe_search())
        
        # Environment info
        if self.current_environment:
            env_frame = ctk.CTkFrame(main, fg_color=COLORS["surface"], height=30)
            env_frame.pack(fill="x", padx=20, pady=(0, 10))
            
            env_display = self.current_environment.get("display", "Unknown environment")
            ctk.CTkLabel(env_frame, text=f"📦 Installing to: {env_display}", 
                        font=("Segoe UI", 10, "bold")).pack(pady=5)
        
        # Results
        results_frame = ctk.CTkFrame(main, fg_color=COLORS["surface"])
        results_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        # Treeview
        self.tree = ttk.Treeview(results_frame, columns=("name", "version", "status"),
                                show="headings", height=15)
        
        style = ttk.Style()
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))
        
        self.tree.column("name", width=350, anchor="w", stretch=True)
        self.tree.column("version", width=200, anchor="center", stretch=False)
        self.tree.column("status", width=200, anchor="center", stretch=False)
        
        self.tree.heading("name", text="Package", anchor="w")
        self.tree.heading("version", text="Version", anchor="center")
        self.tree.heading("status", text="Status", anchor="center")
        
        style.configure("Treeview", font=("Segoe UI", 11), rowheight=35)
        
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<Double-1>", lambda e: self.install_selected())
        
        # No results message
        self.no_results_frame = ctk.CTkFrame(results_frame, fg_color="transparent")
        self.no_results_frame.pack(expand=True, fill="both")
        self.no_results_frame.pack_forget()
        
        self.no_results_label = ctk.CTkLabel(
            self.no_results_frame, 
            text="🔍 No Packages Found",
            font=("Segoe UI", 16, "bold"),
            text_color=COLORS["subtext"]
        )
        self.no_results_label.pack(expand=True, pady=20)
        
        # Status bar
        self.status = ctk.CTkLabel(main, text="Ready - Type to search", height=25,
                                  font=("Segoe UI", 10, "bold"))
        self.status.pack(fill="x", padx=20, pady=(0, 10))
        
        # Bottom buttons container
        self.bottom_container = ctk.CTkFrame(main, fg_color=COLORS["bg"], height=70)
        self.bottom_container.pack(fill="x", padx=20, pady=(0, 15))
        self.bottom_container.pack_propagate(False)
        self.bottom_container.pack_forget()
        
        center_container = ctk.CTkFrame(self.bottom_container, fg_color="transparent")
        center_container.pack(expand=True, fill="both", pady=10)
        
        self.install_btn = ctk.CTkButton(
            center_container, 
            text="📦 Install Selected", 
            width=150,
            height=35,
            font=("Segoe UI", 12, "bold"),
            state="disabled", 
            command=self.install_selected,
            fg_color=COLORS["success"],
            hover_color=COLORS["accent_hover"]
        )
        self.install_btn.pack(side="left", padx=(0, 20))
        
        close_btn = ctk.CTkButton(
            center_container, 
            text="✕ Close", 
            width=120,
            height=35,
            font=("Segoe UI", 12, "bold"),
            command=self.close,
            fg_color=COLORS["surface"],
            hover_color=COLORS["card"],
            text_color=COLORS["text"]
        )
        close_btn.pack(side="right", padx=(20, 0))
        
        self.entry.focus_set()
    
    def _update_search_btn(self):
        """Update search button state based on input."""
        text = self.entry.get().strip()
        self.search_btn.configure(state="normal" if text else "disabled")
    
    def _safe_search(self):
        """Perform search in background thread."""
        term = self.entry.get().strip()
        if not term:
            return
        
        self.search_btn.configure(state="disabled", text="Searching...")
        self.status.configure(text=f"Searching for '{term}'...")
        self.entry.configure(state="disabled")
        
        self.no_results_frame.pack_forget()
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        
        def search_task():
            try:
                results = []
                json_results = self.core._search_json_api(term)
                if json_results:
                    results = json_results
                else:
                    web_results = self.core._search_web_scrape(term)
                    if web_results:
                        results = web_results
                
                processed = self.core._process_search_results(results) if not self.core._shutting_down.is_set() else []
                self.after_idle(lambda: self._update_results_safely(processed))
                
            except Exception as e:
                logger.error(f"Search failed: {e}")
                self.after_idle(lambda: self._update_results_safely([]))
        
        threading.Thread(target=search_task, daemon=True).start()
    
    def _update_results_safely(self, results):
        """Update search results in main thread."""
        try:
            if not self.winfo_exists():
                return
            
            self.search_btn.configure(state="normal", text="Search")
            self.entry.configure(state="normal")
            self.results = results or []
            
            children = list(self.tree.get_children())
            if children:
                self.tree.delete(*children)
            
            if not results:
                self.tree.pack_forget()
                self.no_results_frame.pack(expand=True, fill="both")
                self.status.configure(text="No packages found")
                self.install_btn.configure(state="disabled", text="📦 Install Selected")
                self.bottom_container.pack_forget()
                return
            else:
                self.no_results_frame.pack_forget()
                self.tree.pack(fill="both", expand=True, padx=1, pady=1)
                self.bottom_container.pack(fill="x", padx=20, pady=(0, 15))
            
            for pkg in results:
                name = pkg.get("name", "")
                version = pkg.get("version", "")
                installed = pkg.get("installed", False)
                
                status = "Installed" if installed else "Not Installed"
                self.tree.insert("", "end", values=(name, f"v{version}", status))
            
            self.status.configure(text=f"Found {len(results)} packages")
            
            if results:
                self.tree.selection_set(self.tree.get_children()[0])
                self.install_btn.configure(state="normal", text="📦 Install Selected")
                
        except Exception as e:
            logger.error(f"Error updating search results: {e}")
            self.search_btn.configure(state="normal", text="Search")
            self.entry.configure(state="normal")
    
    def search(self):
        """Legacy search method."""
        self._safe_search()
    
    def update_results(self, results):
        """Legacy update method."""
        self._update_results_safely(results)
    
    def install_selected(self):
        """Install selected package."""
        selection = self.tree.selection()
        if not selection:
            return
        
        item = self.tree.item(selection[0])
        package_name = item['values'][0]
        
        pkg = next((p for p in self.results if p.get("name") == package_name), None)
        if not pkg:
            return
        
        is_installed = pkg.get("installed", False)
        version = pkg.get("version", "latest")
        
        action = "update" if is_installed else "install"
        
        env_info = ""
        if self.current_environment:
            env_name = self.current_environment.get("display", "").split("(")[0].strip()
            env_info = f"\n\nEnvironment: {env_name}"
        
        message = f"{action.title()} {package_name} v{version}?{env_info}"
        
        def handler(choice):
            if choice == action.title():
                self._install(package_name, version, action)
        
        MessageBox(self, f"Confirm {action.title()}", message, 
                  buttons=[action.title(), "Cancel"], callback=handler)
    
    def _install(self, package, version, action):
        """Start package installation."""
        progress = ProgressDialog(
            self, 
            package, 
            version, 
            action, 
            environment_info=self.current_environment
        )
        
        def progress_callback(data):
            safe_ui_callback(progress, lambda: progress.update_progress(data))
        
        def completion_callback(success, message):
            safe_ui_callback(self, lambda: self._handle_install_result(success, message, package))
        
        self.core.install_pypi_package(package, version, completion_callback, progress_callback)
    
    def _handle_install_result(self, success, message, package):
        """Handle installation result."""
        if success:
            self.status.configure(text=f"Successfully installed {package}")
            current = self.entry.get().strip()
            if current:
                self._safe_search()
        else:
            self.status.configure(text=f"Failed: {message[:50]}...")
    
    def clear(self):
        """Clear search results."""
        self.entry.delete(0, tk.END)
        children = list(self.tree.get_children())
        if children:
            self.tree.delete(*children)
        self.status.configure(text="Ready - Type to search")
        self.install_btn.configure(state="disabled", text="📦 Install Selected")
        
        self.no_results_frame.pack_forget()
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.bottom_container.pack_forget()
        
        self.entry.focus_set()
    
    def close(self):
        """Close dialog."""
        if self._is_closing:
            return
        self._is_closing = True
        if self.on_close:
            self.on_close()
        self.destroy()


class MessageBox(ctk.CTkToplevel):
    """Custom message box dialog."""
    
    def __init__(self, parent, title, message, buttons=("OK",), callback=None, icon="ℹ️"):
        super().__init__(parent)
        
        set_window_icon(self, parent)
        
        self.overrideredirect(True)
        self.configure(fg_color=COLORS["bg"])
        self.transient(parent)
        self.grab_set()
        
        # Center on parent
        try:
            parent_x = parent.winfo_x()
            parent_y = parent.winfo_y()
            parent_w = parent.winfo_width() or 800
            parent_h = parent.winfo_height() or 600
            
            win_w = 400
            win_h = 180
            x = parent_x + (parent_w - win_w) // 2
            y = parent_y + (parent_h - win_h) // 2
            
            screen_width = parent.winfo_screenwidth()
            screen_height = parent.winfo_screenheight()
            
            x = max(0, min(x, screen_width - win_w))
            y = max(0, min(y, screen_height - win_h))
            
            self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception as e:
            self.geometry("400x180")
            logger.debug(f"Error calculating message box position: {e}")
        
        # Title bar
        title_bar = ctk.CTkFrame(self, fg_color=COLORS["surface"], height=35)
        title_bar.pack(fill="x")
        
        ctk.CTkLabel(title_bar, text=f"  {icon}  {title}", 
                    font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)
        
        close_btn = ctk.CTkButton(
            title_bar, 
            text="✕", 
            width=30,
            height=30,
            fg_color="transparent",
            hover_color=COLORS["danger"],
            text_color=COLORS["text"],
            command=self._close_with_callback
        )
        close_btn.pack(side="right", padx=5)
        
        # Message
        ctk.CTkLabel(self, text=message, wraplength=380,
                    font=("Segoe UI", 12)).pack(expand=True, padx=20, pady=20)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        for btn_text in buttons:
            color = COLORS["danger"] if btn_text in ["Cancel", "No"] else COLORS["accent"]
            btn = ctk.CTkButton(btn_frame, text=btn_text, width=90,
                               fg_color=color, 
                               command=lambda b=btn_text: self._click(b, callback))
            btn.pack(side="left", padx=5)
        
        self.bind("<Escape>", lambda e: self._close_with_callback())
    
    def _close_with_callback(self):
        """Handle close button click."""
        self.destroy()
    
    def _click(self, button, callback):
        """Handle button click."""
        if callback:
            try:
                callback(button)
            except Exception:
                pass
        self.destroy()
    
    @staticmethod
    def show_success(parent, title, message):
        """Show success message box."""
        MessageBox(parent, title, message, icon="✅")
    
    @staticmethod
    def show_error(parent, title, message):
        """Show error message box."""
        MessageBox(parent, title, message, icon="❌")


def setup_styles():
    """Setup ttk styles."""
    style = ttk.Style()
    style.theme_use('clam')
    
    # Progress bars
    style.configure("Moving.Horizontal.TProgressbar", 
                   thickness=10, 
                   background=COLORS["accent"],
                   troughcolor=COLORS["card"],
                   borderwidth=0)
    
    # Treeview
    style.configure("Treeview", 
                   background=COLORS["card"], 
                   foreground=COLORS["text"],
                   fieldbackground=COLORS["card"], 
                   rowheight=35,
                   font=("Segoe UI", 11))
    
    style.configure("Treeview.Heading", 
                   background=COLORS["surface"],
                   foreground=COLORS["subtext"], 
                   relief="flat",
                   font=("Segoe UI", 11, "bold"))
    
    style.map("Treeview", background=[("selected", COLORS["accent"])])


def show_dependencies(parent, package, dependencies):
    """Show dependencies window."""
    win = ctk.CTkToplevel(parent)
    win.title(f"Dependencies - {package}")
    
    set_window_icon(win, parent)
    
    try:
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_w = parent.winfo_width() or 800
        parent_h = parent.winfo_height() or 600
        
        win_w = 400
        win_h = 400
        x = parent_x + (parent_w - win_w) // 2
        y = parent_y + (parent_h - win_h) // 2
        
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        
        x = max(0, min(x, screen_width - win_w))
        y = max(0, min(y, screen_height - win_h))
        
        win.geometry(f"{win_w}x{win_h}+{x}+{y}")
    except Exception as e:
        win.geometry("400x400")
        logger.debug(f"Error calculating dependencies window position: {e}")
    
    win.configure(fg_color=COLORS["bg"])
    win.transient(parent)
    
    text = tk.Text(win, bg=COLORS["card"], fg=COLORS["text"], wrap="word",
                  font=("Segoe UI", 11))
    text.pack(fill="both", expand=True, padx=10, pady=10)
    
    if dependencies:
        content = "• " + "\n• ".join(dependencies)
    else:
        content = "No dependencies"
    
    text.insert("1.0", content)
    text.config(state="disabled")
    
    ctk.CTkButton(win, text="Close", command=win.destroy,
                 font=("Segoe UI", 11, "bold")).pack(pady=10)
    
    win.bind("<Escape>", lambda e: win.destroy())
    
    return win