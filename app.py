"""PyScope Main Application"""

import tkinter as tk
import customtkinter as ctk
import threading
import sys
import os
import time
import hashlib
import traceback
from tkinter import ttk
from PIL import Image, ImageTk
from collections import deque

# Add current directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from utils import COLORS, logger, run_pip_with_real_progress
    from ui import (
        MessageBox,
        setup_styles,
        show_dependencies,
        SearchDialog,
        EntryContextMenu,
        ProgressDialog,
        set_window_icon
    )
    logger.info("UI utilities imported successfully")
except ImportError as e:
    logger.error(f"Failed to import UI utilities: {e}")
    COLORS = {"bg": "#0a0a0a", "surface": "#121212", "card": "#1a1a1a", "accent": "#00bcd4",
              "accent_hover": "#0097a7", "text": "#ffffff", "subtext": "#888888", 
              "success": "#4caf50", "warning": "#ff9800", "danger": "#f44336", 
              "border": "#2a2a2a"}
    
    def set_window_icon(window, parent=None):
        pass
    
    def MessageBox(*args, **kwargs):
        pass
    
    def setup_styles():
        pass
    
    def show_dependencies(*args, **kwargs):
        pass
    
    def SearchDialog(*args, **kwargs):
        pass
    
    def EntryContextMenu():
        pass
    
    def ProgressDialog(*args, **kwargs):
        pass

# Import PackageManagerCore
try:
    from core import PackageManagerCore
    logger.info("PackageManagerCore imported successfully")
except ImportError as e:
    logger.error(f"Failed to import PackageManagerCore: {e}")
    
    class PackageManagerCore:
        def __init__(self):
            self._shutting_down = False
            self.packages = []
        
        def set_shutting_down(self, value): 
            self._shutting_down = value
        def is_shutting_down(self): 
            return self._shutting_down
        def load_packages(self, callback): 
            if callback: 
                callback()
        def load_packages_with_cache(self, callback, env_id=None): 
            if callback: 
                callback()
        def refresh_packages_data(self): 
            return [], 0, 0
        def filter_packages(self, mode): 
            return []
        def search_packages(self, term): 
            return []
        def get_package_by_name(self, name): 
            return None
        def check_updates(self, *args, **kwargs): 
            pass
        def search_pypi_packages(self, *args, **kwargs): 
            pass
        def install_pypi_package(self, *args, **kwargs): 
            pass
        def clear_rate_limit(self, name): 
            pass
        def update_package_status(self, *args, **kwargs): 
            pass
        def cancel_check(self): 
            pass
        def clear_all_cache(self): 
            pass
        def save_packages_to_cache(self, env_id): 
            pass
        def set_pip_command(self, cmd): 
            pass
        def get_pip_command(self): 
            return [sys.executable, "-m", "pip"]
        def check_single_package(self, pkg_name, callback):
            if callback:
                callback(False, "Fallback mode")

# Import EnvironmentManager
try:
    from environments import EnvironmentManager
    logger.info("EnvironmentManager imported successfully")
except ImportError as e:
    logger.error(f"Failed to import EnvironmentManager: {e}")
    
    class EnvironmentManager:
        def __init__(self):
            self.current_env = {
                "type": "system",
                "python_path": sys.executable,
                "pip_path": None,
                "display": "System Python",
                "version": "Unknown"
            }
            self.all_environments = [self.current_env]
        
        def refresh(self): 
            pass
        def get_all_environments(self): 
            return [self.current_env]
        def set_environment(self, env): 
            self.current_env = env
        def get_pip_command(self): 
            return [sys.executable, "-m", "pip"]
        def get_python_command(self): 
            return sys.executable
        def get_current_display(self): 
            return "System Python"


class ThreadManager:
    """Manages threads to prevent UI freezing and memory leaks."""
    
    def __init__(self, max_workers=3):
        self.max_workers = max_workers
        self.active_threads = []
        self.completed_threads = deque(maxlen=100)
        self.lock = threading.RLock()
    
    def submit_task(self, task_func, task_name=""):
        """Submit a task with thread management and cleanup."""
        with self.lock:
            self._cleanup_finished()
            
            if len(self.active_threads) >= self.max_workers:
                logger.warning(f"Thread limit reached, waiting for slot: {task_name}")
                self._wait_for_slot()
            
            thread = threading.Thread(
                target=self._wrap_task(task_func, task_name),
                daemon=True,
                name=f"Task-{task_name}"
            )
            self.active_threads.append(thread)
            thread.start()
            logger.debug(f"Started thread: {task_name}")
    
    def _wrap_task(self, task_func, task_name):
        """Wrap task with error handling and cleanup."""
        def wrapped():
            start_time = time.time()
            try:
                task_func()
            except Exception as e:
                logger.error(f"Task {task_name} failed: {e}")
            finally:
                end_time = time.time()
                with self.lock:
                    if threading.current_thread() in self.active_threads:
                        self.active_threads.remove(threading.current_thread())
                        self.completed_threads.append({
                            'name': task_name,
                            'duration': end_time - start_time,
                            'timestamp': time.time()
                        })
        return wrapped
    
    def _cleanup_finished(self):
        """Clean up finished threads."""
        with self.lock:
            self.active_threads = [t for t in self.active_threads if t.is_alive()]
    
    def _wait_for_slot(self, timeout=5):
        """Wait for a thread slot to become available."""
        start_time = time.time()
        while len(self.active_threads) >= self.max_workers:
            if time.time() - start_time > timeout:
                raise TimeoutError("No thread slot available")
            time.sleep(0.1)
            self._cleanup_finished()
    
    def get_stats(self):
        """Get thread manager statistics."""
        with self.lock:
            return {
                'active': len(self.active_threads),
                'max_workers': self.max_workers,
                'recent_completed': len(self.completed_threads)
            }
    
    def shutdown(self):
        """Shutdown thread manager and clean up."""
        with self.lock:
            self.active_threads.clear()
            self.completed_threads.clear()


class PackageManagerApp(ctk.CTk):
    """Main application class for PyScope Package Manager."""
    
    def __init__(self):
        super().__init__()
        
        # Logo setup
        self.logo_path = self._find_logo_file()
        self.logo_image = None
        self.logo_label = None
        
        # AppUserModelID for Windows
        if sys.platform == "win32":
            try:
                import ctypes
                myappid = 'PyScope.PackageManager.1.0'
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except Exception as e:
                logger.error(f"Failed to set AppUserModelID: {e}")
        
        self.core = PackageManagerCore()
        self._search_dialog = None
        self._active_dialogs = {}
        self._update_in_progress = False
        
        self.thread_manager = ThreadManager(max_workers=3)
        self._update_counter = 0
        
        self.env_manager = EnvironmentManager()
        self._current_env_id = self._get_environment_id()
        
        self.core.set_pip_command(self.env_manager.get_pip_command())
        
        self.title("PyScope - Python Package Manager")
        self.geometry("1300x800")
        self.configure(fg_color=COLORS["bg"])
        
        # Setup window icon
        self._setup_window_icon()
        
        try:
            self._build_ui()
            self._bind_shortcuts()
            self._load_packages()
        except Exception as e:
            logger.error(f"Failed to initialize app: {e}")
            self.destroy()
            return
        
        self.protocol("WM_DELETE_WINDOW", self._simple_shutdown)
        logger.info("Application initialized successfully")
    
    def _find_logo_file(self):
        """Find logo file in common locations."""
        logo_paths = [
            "logo.png",
            "logo_transparent.png",
            "logo.jpg",
            "logo.jpeg",
            "assets/logo.png",
            "images/logo.png",
            os.path.join(current_dir, "logo.png"),
        ]
        
        for path in logo_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def _setup_window_icon(self):
        """Setup window icon with larger size for better taskbar display."""
        try:
            icon_paths = [
                "logo.ico",
                "logo_transparent.png",
                "logo.png",
                "assets/logo.ico",
                "images/logo.ico",
                os.path.join(current_dir, "logo.ico"),
                os.path.join(current_dir, "logo_transparent.png"),
                os.path.join(current_dir, "logo.png"),
            ]
            
            icon_path_used = None
            
            for icon_path in icon_paths:
                if os.path.exists(icon_path):
                    icon_path_used = icon_path
                    break
            
            self.icon_path = icon_path_used

            if icon_path_used:
                # Windows .ico support
                if sys.platform.startswith("win") and icon_path_used.endswith(".ico"):
                    try:
                        self.iconbitmap(icon_path_used)
                        logger.info(f"Applied .ico icon: {icon_path_used}")
                    except Exception as e:
                        logger.debug(f"iconbitmap failed: {e}")

                from PIL import Image, ImageTk
                image = Image.open(icon_path_used)
                
                if image.mode != 'RGBA':
                    image = image.convert('RGBA')
                
                image = self._crop_black_background(image)
                
                # Multiple sizes for different contexts
                image_256 = image.resize((256, 256), Image.Resampling.LANCZOS)
                image_128 = image.resize((128, 128), Image.Resampling.LANCZOS)
                image_64 = image.resize((64, 64), Image.Resampling.LANCZOS)
                image_32 = image.resize((32, 32), Image.Resampling.LANCZOS)
                
                photo_image_256 = ImageTk.PhotoImage(image_256)
                photo_image_128 = ImageTk.PhotoImage(image_128)
                photo_image_64 = ImageTk.PhotoImage(image_64)
                photo_image_32 = ImageTk.PhotoImage(image_32)
                
                self.iconphoto(True, photo_image_256, photo_image_128, photo_image_64, photo_image_32)
                self.window_icon = photo_image_64
                
                if not hasattr(self, '_icon_photos'):
                    self._icon_photos = []
                self._icon_photos.extend([photo_image_256, photo_image_128, photo_image_64, photo_image_32])
                
                logger.info(f"Window icon set successfully from: {icon_path_used}")
                
        except Exception as e:
            logger.error(f"Failed to setup window icon: {e}")
    
    def _crop_black_background(self, image):
        """Crop black background from around logo."""
        try:
            pixels = image.load()
            width, height = image.size
            
            min_x, min_y = width, height
            max_x, max_y = 0, 0
            
            for y in range(height):
                for x in range(width):
                    pixel = pixels[x, y]
                    r, g, b = pixel[0], pixel[1], pixel[2]
                    a = pixel[3] if len(pixel) > 3 else 255
                    
                    if not (r < 25 and g < 25 and b < 25) and a > 50:
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)
            
            if min_x >= max_x or min_y >= max_y:
                return image
            
            padding = 5
            min_x = max(0, min_x - padding)
            min_y = max(0, min_y - padding)
            max_x = min(width, max_x + padding)
            max_y = min(height, max_y + padding)
            
            cropped = image.crop((min_x, min_y, max_x, max_y))
            
            w, h = cropped.size
            size = max(w, h)
            square = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            offset = ((size - w) // 2, (size - h) // 2)
            square.paste(cropped, offset)
            
            return square
            
        except Exception as e:
            logger.debug(f"Could not crop background: {e}")
            return image
    
    def _simple_shutdown(self):
        """Simple shutdown procedure."""
        logger.info("Shutting down...")
        self.core.set_shutting_down(True)
        
        self.thread_manager.shutdown()
        
        try:
            self.check_progress_bar.stop()
        except:
            pass
        
        if self._search_dialog:
            try:
                self._search_dialog.destroy()
            except:
                pass
        
        self.after(50, self.destroy)
    
    def _get_environment_id(self):
        """Generate environment ID."""
        try:
            current_env = self.env_manager.current_env
            if not current_env:
                return "default"
            
            python_path = current_env.get("python_path", "")
            env_path = current_env.get("path", "")
            
            if python_path:
                return hashlib.md5(python_path.encode()).hexdigest()[:16]
            elif env_path:
                return hashlib.md5(env_path.encode()).hexdigest()[:16]
            else:
                return "default"
        except Exception:
            return "default"
    
    def _bind_shortcuts(self):
        """Bind keyboard shortcuts."""
        try:
            shortcuts = [
                ("<Control-f>", lambda e: self.open_pypi_search()),
                ("<Control-F>", lambda e: self.open_pypi_search()),
                ("<F5>", lambda e: self._load_packages()),
                ("<Control-q>", lambda e: self._simple_shutdown()),
            ]
            
            for shortcut, callback in shortcuts:
                self.bind(shortcut, callback)
        except Exception as e:
            logger.error(f"Error binding shortcuts: {e}")
    
    def _cleanup_stuck_dialog(self):
        """Clean up stuck dialog."""
        try:
            if self._search_dialog:
                if (hasattr(self._search_dialog, 'winfo_exists') and 
                    self._search_dialog.winfo_exists() and
                    not getattr(self._search_dialog, '_is_closing', True)):
                    try:
                        self._search_dialog.deiconify()
                        self._search_dialog.lift()
                        return
                    except Exception:
                        pass
                self._search_dialog = None
            
            if hasattr(self, 'btn_search_pypi') and self.btn_search_pypi.winfo_exists():
                self.btn_search_pypi.configure(
                    state="normal",
                    text="+ Install New",
                    fg_color=COLORS["success"]
                )
        except Exception as e:
            logger.error(f"Error cleaning up dialog: {e}")
    
    def _build_ui(self):
        """Build main application UI."""
        try:
            header = ctk.CTkFrame(self, fg_color=COLORS["surface"], height=100)
            header.pack(fill="x")
            header.pack_propagate(False)
            
            title_frame = ctk.CTkFrame(header, fg_color="transparent")
            title_frame.pack(side="left", padx=30, pady=15)
            
            logo_title_frame = ctk.CTkFrame(title_frame, fg_color="transparent")
            logo_title_frame.pack(anchor="w")
            
            # Load logo if available
            if self.logo_path and os.path.exists(self.logo_path):
                try:
                    from PIL import Image
                    image = Image.open(self.logo_path)
                    
                    if image.mode != 'RGBA':
                        image = image.convert('RGBA')
                    
                    image = image.resize((120, 120), Image.Resampling.LANCZOS)
                    
                    self.logo_image = ctk.CTkImage(
                        light_image=image,
                        dark_image=image,
                        size=(120 , 120)
                    )
                    
                    self.logo_label = ctk.CTkLabel(
                        logo_title_frame, 
                        image=self.logo_image,
                        text=""
                    )
                    self.logo_label.pack(side="left", padx=(0, 25))
                    
                except Exception as e:
                    logger.error(f"Failed to load logo: {e}")
            
            text_frame = ctk.CTkFrame(logo_title_frame, fg_color="transparent")
            text_frame.pack(side="left")
            
            ctk.CTkLabel(
                text_frame, 
                text="PyScope",
                font=("Segoe UI", 28, "bold")
            ).pack(anchor="w")
            
            self.stats_lbl = ctk.CTkLabel(
                text_frame, 
                text="Loading packages...",
                font=("Segoe UI", 14),
                text_color="#00FFFF"
            )
            self.stats_lbl.pack(anchor="w", pady=(8, 0))
            
            env_frame = ctk.CTkFrame(header, fg_color="transparent")
            env_frame.pack(side="right", padx=30, pady=15)
            
            ctk.CTkLabel(env_frame, text="Environment:", 
                        font=("Segoe UI", 12)).pack(side="left", padx=(0, 15))
            
            self.env_combo = ctk.CTkComboBox(
                env_frame,
                values=[env["display"] for env in self.env_manager.get_all_environments()],
                width=400,
                height=40,
                command=self._on_env_change,
                state="readonly",
                font=("Segoe UI", 12)
            )
            self.env_combo.pack(side="left")
            self.env_combo.set(self.env_manager.get_current_display())
            
            self.env_refresh_btn = ctk.CTkButton(
                env_frame, text="Refresh", width=50, height=40,
                command=self._refresh_environments,
                fg_color=COLORS["surface"],
                font=("Segoe UI", 11)
            )
            self.env_refresh_btn.pack(side="left", padx=(15, 0))
            
            spacer_frame = ctk.CTkFrame(self, fg_color=COLORS["bg"], height=10)
            spacer_frame.pack(fill="x")
            spacer_frame.pack_propagate(False)
            
            control_row = ctk.CTkFrame(self, fg_color=COLORS["bg"], height=80)
            control_row.pack(fill="x", padx=30, pady=15)
            control_row.pack_propagate(False)
            
            filter_frame = ctk.CTkFrame(control_row, fg_color="transparent")
            filter_frame.pack(side="left")
            
            self.filters = {}
            for text in ["All", "Outdated", "Updated"]:
                btn = ctk.CTkButton(
                    filter_frame, text=text, width=110, height=40,
                    fg_color=COLORS["accent"] if text == "All" else COLORS["surface"],
                    command=lambda x=text: self.apply_filter(x),
                    font=("Segoe UI", 12, "bold")
                )
                btn.pack(side="left", padx=8)
                self.filters[text] = btn
            
            center_frame = ctk.CTkFrame(control_row, fg_color="transparent")
            center_frame.pack(side="left", fill="both", expand=True, padx=30)
            
            self.search_entry = ctk.CTkEntry(
                center_frame,
                placeholder_text="Search in your libraries",
                width=500, height=40,
                fg_color=COLORS["card"],
                font=("Segoe UI", 13)
            )
            self.search_entry.pack(side="left", fill="x", expand=True)
            self.search_entry.bind("<KeyRelease>", self.on_search)
            
            try:
                EntryContextMenu.bind_to_entry(self.search_entry)
            except Exception:
                pass
            
            action_frame = ctk.CTkFrame(control_row, fg_color="transparent")
            action_frame.pack(side="right")
            
            self.btn_check = ctk.CTkButton(
                action_frame, 
                text="Check", 
                width=120,
                height=40,
                fg_color=COLORS["accent"], 
                command=self.check_updates,
                text_color=COLORS["text"],
                text_color_disabled=COLORS["text"],
                font=("Segoe UI", 12, "bold")
            )
            self.btn_check.pack(side="left", padx=15)
            
            self.btn_search_pypi = ctk.CTkButton(
                action_frame, text="+ Install New", width=140, height=40,
                fg_color=COLORS["success"], 
                command=self.open_pypi_search,
                font=("Segoe UI", 12, "bold")
            )
            self.btn_search_pypi.pack()
            
            self.progress_container = ctk.CTkFrame(self, fg_color=COLORS["bg"], height=45)
            self.progress_container.pack(fill="x", padx=30, pady=(10, 15))
            self.progress_container.pack_propagate(False)
            
            style = ttk.Style()
            style.theme_use('clam')
            style.configure("Moving.Horizontal.TProgressbar",
                          background=COLORS["accent"],
                          troughcolor=COLORS["card"],
                          borderwidth=2,
                          relief="flat",
                          thickness=20)
            
            self.check_progress_bar = ttk.Progressbar(
                self.progress_container,
                style="Moving.Horizontal.TProgressbar",
                mode='indeterminate',
                length=900,
                maximum=100
            )
            self.check_progress_bar.pack(fill="x", expand=True)
            
            self.progress_text = ctk.CTkLabel(
                self.progress_container,
                text="Ready to check updates",
                font=("Segoe UI", 12, "bold"),
                text_color=COLORS["subtext"]
            )
            self.progress_text.pack(pady=(6, 0))
            
            self.progress_container.pack_forget()
            
            try:
                setup_styles()
            except Exception:
                pass
            
            self.tree_container = ctk.CTkFrame(self, fg_color=COLORS["surface"])
            self.tree_container.pack(fill="both", expand=True, padx=30, pady=15)
            
            sb = ttk.Scrollbar(self.tree_container)
            sb.pack(side="right", fill="y")
            
            self.tree = ttk.Treeview(
                self.tree_container,
                columns=("pkg", "ver", "lat", "stat"),
                show="headings",
                yscrollcommand=sb.set
            )
            sb.config(command=self.tree.yview)
            
            columns = [
                ("pkg", "PACKAGE", 500, "w"),
                ("ver", "INSTALLED", 250, "center"),
                ("lat", "LATEST", 250, "center"),
                ("stat", "STATUS", 250, "center")
            ]
            
            for col, text, width, anchor in columns:
                self.tree.column(col, width=width, anchor=anchor)
                self.tree.heading(col, text=text, anchor=anchor)
            
            self.tree.pack(fill="both", expand=True)
            self.tree.bind("<Double-1>", self.on_item_click)
            
            self.tree.tag_configure("updated", foreground=COLORS["success"])
            self.tree.tag_configure("outdated", foreground=COLORS["warning"])
            
            self.status_indicator = ctk.CTkLabel(
                self, text="", height=30
            )
            self.status_indicator.pack(side="bottom", fill="x", padx=30, pady=10)
            
        except Exception as e:
            logger.error(f"UI build failed: {e}")
            raise
    
    def _refresh_environments(self):
        """Refresh environment list."""
        self.env_refresh_btn.configure(state="disabled", text="Refreshing...")
        self.status_indicator.configure(text="Refreshing environments...")
        
        def refresh_task():
            try:
                if self._current_env_id:
                    try:
                        self.core.save_packages_to_cache(self._current_env_id)
                    except Exception:
                        pass
                
                self.env_manager.refresh()
                envs = self.env_manager.get_all_environments()
                displays = [env["display"] for env in envs]
                
                self.after(0, lambda: [
                    self.env_combo.configure(values=displays),
                    self.env_combo.set(self.env_manager.get_current_display()),
                    self.env_refresh_btn.configure(state="normal", text="Refresh"),
                    self.status_indicator.configure(text="Environments refreshed"),
                    self._current_env_id == self._get_environment_id(),
                    self._load_packages(),
                    self.after(3000, lambda: self.status_indicator.configure(text=""))
                ])
            except Exception as e:
                logger.error(f"Failed to refresh environments: {e}")
                self.after(0, lambda: [
                    self.env_refresh_btn.configure(state="normal", text="Refresh"),
                    self.status_indicator.configure(text=f"Failed: {str(e)[:50]}"),
                    self.after(3000, lambda: self.status_indicator.configure(text=""))
                ])
        
        self.thread_manager.submit_task(refresh_task, "refresh_environments")
    
    def _on_env_change(self, selection):
        """Handle environment change."""
        try:
            if self._update_in_progress:
                self.core.cancel_check()
                try:
                    self.check_progress_bar.stop()
                except:
                    pass
                self._update_in_progress = False
                self.btn_check.configure(
                    text="Check",
                    fg_color=COLORS["accent"],
                    state="normal"
                )
                self.progress_container.pack_forget()
            
            if self._current_env_id:
                try:
                    self.core.save_packages_to_cache(self._current_env_id)
                except Exception:
                    pass
            
            envs = self.env_manager.get_all_environments()
            selected_env = next((env for env in envs if env["display"] == selection), None)
            
            if selected_env:
                self.env_manager.set_environment(selected_env)
                
                try:
                    self.core.clear_all_cache()
                except Exception:
                    pass
                
                pip_cmd = self.env_manager.get_pip_command()
                self.core.set_pip_command(pip_cmd)
                
                self._current_env_id = self._get_environment_id()
                
                self.status_indicator.configure(
                    text=f"Switching to {selected_env.get('display', 'Unknown')}"
                )
                
                for item in self.tree.get_children():
                    self.tree.delete(item)
                
                self.stats_lbl.configure(text="Loading packages from new environment...")
                self._load_packages()
                
                self.after(2000, lambda: self.status_indicator.configure(
                    text=f"Using {selected_env.get('display', 'Unknown')}"
                ))
                self.after(5000, lambda: self.status_indicator.configure(text=""))
        except Exception as e:
            logger.error(f"Failed to change environment: {e}")
            if hasattr(self, 'env_combo'):
                self.env_combo.set(self.env_manager.get_current_display())
    
    def open_pypi_search(self):
        """Open PyPI search dialog."""
        if self.core.is_shutting_down():
            return
        
        self._cleanup_stuck_dialog()
        
        self.btn_search_pypi.configure(
            state="disabled",
            text="Opening...",
            fg_color=COLORS["surface"]
        )
        
        if self._search_dialog:
            try:
                if (self._search_dialog.winfo_exists() and 
                    not getattr(self._search_dialog, '_is_closing', True)):
                    self._search_dialog.deiconify()
                    self._search_dialog.lift()
                    self._search_dialog.focus_force()
                    
                    self.btn_search_pypi.configure(
                        state="normal",
                        text="+ Install New",
                        fg_color=COLORS["success"]
                    )
                    return
            except Exception:
                pass
            self._search_dialog = None
        
        def on_dialog_closed():
            self._search_dialog = None
            self.btn_search_pypi.configure(
                state="normal",
                text="+ Install New",
                fg_color=COLORS["success"]
            )
        
        def create_dialog():
            try:
                self._search_dialog = SearchDialog(
                    parent=self,
                    core=self.core,
                    on_close=on_dialog_closed,
                    current_environment=self.env_manager.current_env
                )
                self.btn_search_pypi.configure(
                    state="normal",
                    text="+ Install New",
                    fg_color=COLORS["success"]
                )
            except Exception as e:
                logger.error(f"Failed to create search dialog: {e}")
                self.btn_search_pypi.configure(
                    state="normal",
                    text="+ Install New",
                    fg_color=COLORS["success"]
                )
        
        self.after_idle(create_dialog)
    
    def _load_packages(self):
        """Load packages from current environment."""
        if self.core.is_shutting_down():
            return
        
        self.status_indicator.configure(text="Loading packages...")
        
        def load_task():
            try:
                self.core.load_packages_with_cache(
                    lambda: self.after(0, self._safe_refresh_after_load), 
                    self._current_env_id
                )
            except Exception as e:
                logger.error(f"Error in load task: {e}")
                self.after(0, lambda: self.status_indicator.configure(text=f"Error: {str(e)[:50]}"))
        
        self.thread_manager.submit_task(load_task, "load_packages")
    
    def _safe_refresh_after_load(self):
        """Safely refresh UI after loading packages."""
        try:
            self.refresh_tree()
            self.status_indicator.configure(text="Packages loaded")
            self.after(3000, lambda: self.status_indicator.configure(text=""))
        except Exception as e:
            logger.error(f"Error refreshing packages: {e}")
            self.status_indicator.configure(text=f"Error: {str(e)[:50]}")
            self.after(3000, lambda: self.status_indicator.configure(text=""))
    
    def refresh_tree(self):
        """Refresh package treeview."""
        if self.core.is_shutting_down():
            return
        
        self.after_idle(self._safe_refresh_tree)
    
    def _safe_refresh_tree(self):
        """Thread-safe tree refresh."""
        if self.core.is_shutting_down() or not self.tree.winfo_exists():
            return
            
        try:
            children = list(self.tree.get_children())
            if children:
                self.tree.delete(*children)
            
            packages, total, outdated = self.core.refresh_packages_data()
            env_display = self.env_manager.get_current_display()
            self.stats_lbl.configure(text=f"Packages: {total} | Updates: {outdated} | {env_display}")
            
            batch_size = 50
            for i in range(0, len(packages), batch_size):
                batch = packages[i:i+batch_size]
                for pkg in batch:
                    icon = "✓" if pkg["stat"] == "Updated" else "▲" if pkg["stat"] == "Outdated" else "[--]"
                    tag = "updated" if pkg["stat"] == "Updated" else "outdated" if pkg["stat"] == "Outdated" else ""
                    
                    values = (pkg["name"], f"v{pkg['ver']}", pkg["lat"], f"{icon} {pkg['stat']}")
                    self.tree.insert("", "end", values=values, tags=(tag,))
                
                if i + batch_size < len(packages):
                    self.update_idletasks()
                    
        except Exception as e:
            logger.error(f"Tree refresh failed: {e}")
    
    def on_search(self, event=None):
        """Handle local search in packages."""
        try:
            term = self.search_entry.get().strip()
            
            children = list(self.tree.get_children())
            if children:
                self.tree.delete(*children)
            
            if not term:
                current_filter = next((key for key, btn in self.filters.items() if btn.cget("fg_color") == COLORS["accent"]), "All")
                self.apply_filter(current_filter)
                return
            
            all_packages, _, _ = self.core.refresh_packages_data()
            results = [pkg for pkg in all_packages if term.lower() in pkg["name"].lower()]
            
            for pkg in results:
                icon = "✓" if pkg["stat"] == "Updated" else "▲" if pkg["stat"] == "Outdated" else "[--]"
                tag = "updated" if pkg["stat"] == "Updated" else "outdated" if pkg["stat"] == "Outdated" else ""
                
                values = (pkg["name"], f"v{pkg['ver']}", pkg["lat"], f"{icon} {pkg['stat']}")
                self.tree.insert("", "end", values=values, tags=(tag,))
        except Exception as e:
            logger.error(f"Search error: {e}")
    
    def apply_filter(self, mode):
        """Apply filter to package list."""
        if self.core.is_shutting_down():
            return
            
        try:
            for key, btn in self.filters.items():
                btn.configure(fg_color=COLORS["accent"] if key == mode else COLORS["surface"])
            
            self.search_entry.delete(0, tk.END)
            
            children = list(self.tree.get_children())
            if children:
                self.tree.delete(*children)
            
            filtered = self.core.filter_packages(mode)
            
            for pkg in filtered:
                icon = "✓" if pkg["stat"] == "Updated" else "▲" if pkg["stat"] == "Outdated" else "[--]"
                tag = "updated" if pkg["stat"] == "Updated" else "outdated" if pkg["stat"] == "Outdated" else ""
                
                values = (pkg["name"], f"v{pkg['ver']}", pkg["lat"], f"{icon} {pkg['stat']}")
                self.tree.insert("", "end", values=values, tags=(tag,))
        except Exception as e:
            logger.error(f"Filter error: {e}")
    
    def check_updates(self):
        """Start update check for all packages."""
        if self.core.is_shutting_down() or self._update_in_progress:
            return
        
        if self._current_env_id:
            try:
                self.core.save_packages_to_cache(self._current_env_id)
            except Exception:
                pass
        
        self._update_in_progress = True
        self.status_indicator.configure(text="Checking for updates...")
        
        self.btn_check.configure(
            text="Checking...",
            fg_color=COLORS["surface"],
            state="disabled"
        )
        
        self.progress_container.pack(fill="x", padx=30, pady=(10, 15))
        
        try:
            self.check_progress_bar.configure(mode='indeterminate')
            self.check_progress_bar.start(15)
        except Exception:
            pass
        
        self.progress_text.configure(text="Checking for updates...")
        
        def ui_start_callback():
            self.after(0, lambda: None)
        
        def ui_package_callback(pkg_name):
            if self.core.is_shutting_down():
                return
            self.after_idle(lambda: self._update_single_package_immediately(pkg_name))
        
        def ui_finish_callback():
            self.after_idle(self._check_updates_finish)
        
        try:
            self.core.check_updates(
                ui_start_callback=ui_start_callback,
                ui_progress_callback=None,
                ui_finish_callback=ui_finish_callback,
                ui_package_callback=ui_package_callback
            )
        except Exception as e:
            logger.error(f"Error starting update check: {e}")
            self._check_updates_finish()
    
    def _check_updates_finish(self):
        """Finish update check and update UI."""
        def finish_task():
            try:
                self.check_progress_bar.stop()
                self.check_progress_bar.configure(mode='determinate', value=0)
            except Exception:
                pass
            
            self.progress_text.configure(text="Update check completed!")
            
            if self._current_env_id:
                try:
                    self.core.save_packages_to_cache(self._current_env_id)
                except Exception:
                    pass
            
            self._update_in_progress = False
            
            self.btn_check.configure(
                text="Check",
                fg_color=COLORS["accent"],
                state="normal"
            )
            
            try:
                self.refresh_tree()
                self.status_indicator.configure(text="Update check complete")
                
                self.after(1500, lambda: [
                    self.progress_container.pack_forget(),
                    self.after(3000, lambda: self.status_indicator.configure(text="")),
                    self.progress_text.configure(text="Ready to check updates")
                ])
            except Exception as e:
                logger.error(f"Error finishing update check: {e}")
        
        self.after_idle(finish_task)
    
    def _update_single_package_immediately(self, pkg_name):
        """Update single package row immediately."""
        def do_update():
            try:
                if not self.winfo_exists():
                    return
                
                pkg = self.core.get_package_by_name(pkg_name)
                if not pkg:
                    return
                
                for item in self.tree.get_children():
                    values = self.tree.item(item)['values']
                    if values and values[0] == pkg_name:
                        icon = "✓" if pkg["stat"] == "Updated" else "▲" if pkg["stat"] == "Outdated" else "[--]"
                        tag = "updated" if pkg["stat"] == "Updated" else "outdated" if pkg["stat"] == "Outdated" else ""
                        
                        new_values = (pkg["name"], f"v{pkg['ver']}", pkg["lat"], f"{icon} {pkg['stat']}")
                        self.tree.item(item, values=new_values, tags=(tag,))
                        
                        self._update_stats_counter()
                        break
            
            except Exception as e:
                logger.error(f"Error updating package row for {pkg_name}: {e}")
        
        self.after_idle(do_update)
    
    def _update_stats_counter(self):
        """Update statistics counter efficiently."""
        try:
            packages, total, outdated = self.core.refresh_packages_data()
            self.stats_lbl.configure(
                text=f"Packages: {total} | Updates: {outdated} | {self.env_manager.get_current_display()}"
            )
        except Exception as e:
            logger.debug(f"Error updating stats: {e}")
    
    def on_item_click(self, event):
        """Handle double-click on package."""
        if self.core.is_shutting_down():
            return
            
        try:
            selection = self.tree.selection()
            if not selection:
                return
            
            package_name = self.tree.item(selection[0])['values'][0]
            package_status = self.tree.item(selection[0])['values'][3]
            package = self.core.get_package_by_name(package_name)
            
            if package:
                if "Outdated" in package_status:
                    self._confirm_package_update(package)
                else:
                    self._show_action_dialog(package)
        except Exception as e:
            logger.error(f"Error on item click: {e}")
    
    def _confirm_package_update(self, pkg):
        """Show update confirmation dialog."""
        def response_handler(choice):
            if choice == "Update":
                try:
                    progress_dialog = ProgressDialog(
                        self, pkg['name'], pkg['lat'], "update",
                        environment_info=self.env_manager.current_env
                    )
                    
                    self._active_dialogs[pkg['name']] = progress_dialog
                    
                    def progress_callback(data):
                        if progress_dialog and progress_dialog.winfo_exists():
                            progress_dialog.update_progress(data)
                    
                    def update_task():
                        try:
                            pip_cmd = self.env_manager.get_pip_command()
                            version_to_install = pkg['lat'] if pkg['lat'] not in ["Unknown", "Error"] else None
                            
                            success, message, _ = run_pip_with_real_progress(
                                ["install", "-U", pkg["name"]],
                                progress_callback=progress_callback,
                                pip_cmd=pip_cmd
                            )
                            
                            if success:
                                self.core.clear_rate_limit(pkg['name'])
                                
                                try:
                                    import subprocess
                                    result = subprocess.run(
                                        pip_cmd + ["show", pkg["name"]],
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                        shell=False
                                    )
                                    
                                    new_version = None
                                    if result.returncode == 0:
                                        for line in result.stdout.split('\n'):
                                            if line.startswith("Version:"):
                                                new_version = line.split(":", 1)[1].strip()
                                                break
                                    
                                    if not new_version:
                                        try:
                                            from importlib.metadata import distribution
                                            dist = distribution(pkg['name'])
                                            new_version = dist.version
                                        except Exception:
                                            new_version = version_to_install or pkg['ver']
                                    
                                    latest_version = new_version
                                    
                                    self.core.update_package_status(
                                        pkg["name"], new_version, latest_version, "Updated"
                                    )
                                    
                                    if self._current_env_id:
                                        self.core.save_packages_to_cache(self._current_env_id)
                                    
                                    self.after(0, lambda: [
                                        progress_dialog.set_completed(True, f"✅ Updated to v{new_version}"),
                                        self._update_single_package_immediately(pkg['name']),
                                        self.after(2000, lambda: self._cleanup_dialog(pkg['name'], progress_dialog))
                                    ])
                                    
                                except Exception as e:
                                    logger.error(f"Error getting updated version for {pkg['name']}: {e}")
                                    self.core.update_package_status(
                                        pkg["name"], 
                                        version_to_install or pkg['lat'], 
                                        version_to_install or pkg['lat'], 
                                        "Updated"
                                    )
                                    
                                    if self._current_env_id:
                                        self.core.save_packages_to_cache(self._current_env_id)
                                    
                                    self.after(0, lambda: [
                                        progress_dialog.set_completed(True, "✅ Update completed successfully"),
                                        self._update_single_package_immediately(pkg['name']),
                                        self.after(2000, lambda: self._cleanup_dialog(pkg['name'], progress_dialog))
                                    ])
                                    
                            else:
                                self.after(0, lambda: [
                                    progress_dialog.set_completed(False, f"❌ {message}"),
                                    self._cleanup_dialog(pkg['name'], progress_dialog)
                                ])
                                
                        except Exception as e:
                            logger.error(f"Update task error for {pkg['name']}: {e}")
                            self.after(0, lambda: [
                                progress_dialog.set_completed(False, f"❌ {str(e)}"),
                                self._cleanup_dialog(pkg['name'], progress_dialog)
                            ])
                    
                    threading.Thread(target=update_task, daemon=True).start()
                except Exception as e:
                    logger.error(f"Error creating update dialog: {e}")
        
        try:
            MessageBox(
                self, "Confirm Update",
                f"Update {pkg['name']} from v{pkg['ver']} to {pkg['lat']}?\n\nEnvironment: {self.env_manager.get_current_display()}",
                buttons=["Update", "Cancel"], callback=response_handler
            )
        except Exception as e:
            logger.error(f"Error showing update confirmation: {e}")
    
    def _cleanup_dialog(self, pkg_name, dialog):
        """Clean up dialog."""
        try:
            if dialog and dialog.winfo_exists():
                dialog.destroy()
            if pkg_name in self._active_dialogs:
                del self._active_dialogs[pkg_name]
        except Exception as e:
            logger.warning(f"Error cleaning up dialog: {e}")
    
    def _show_action_dialog(self, pkg):
        """Show package management dialog."""
        try:
            current_pkg = self.core.get_package_by_name(pkg['name'])
            if current_pkg:
                pkg = current_pkg
            
            dialog = ctk.CTkToplevel(self)
            dialog.configure(fg_color=COLORS["bg"])
            dialog.transient(self)
            dialog.grab_set()
            
            set_window_icon(dialog, self)
            
            dialog_width = 480
            dialog_height = 580
            
            main_x = self.winfo_x()
            main_y = self.winfo_y()
            main_width = self.winfo_width()
            main_height = self.winfo_height()
            
            x = main_x + (main_width - dialog_width) // 2
            y = main_y + (main_height - dialog_height) // 2
            
            dialog.geometry(f"{dialog_width}x{dialog_height}+{max(0, x)}+{max(0, y)}")
            dialog.title(f"📦 {pkg['name']}")
            
            main_frame = ctk.CTkFrame(dialog, fg_color=COLORS["surface"], corner_radius=12)
            main_frame.pack(fill="both", expand=True, padx=15, pady=15)
            
            header_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            header_frame.pack(fill="x", padx=20, pady=(20, 15))
            
            ctk.CTkLabel(
                header_frame,
                text=pkg['name'],
                font=("Segoe UI", 22, "bold"),
                text_color=COLORS["text"]
            ).pack(anchor="w")
            
            ctk.CTkLabel(
                header_frame,
                text=f"📁 {self.env_manager.get_current_display()}",
                font=("Segoe UI", 11),
                text_color=COLORS["subtext"]
            ).pack(anchor="w", pady=(5, 0))
            
            ctk.CTkFrame(main_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=20, pady=(0, 20))
            
            info_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card"], corner_radius=8)
            info_frame.pack(fill="x", padx=20, pady=(0, 25))
            
            versions_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
            versions_frame.pack(fill="x", padx=20, pady=15)
            
            installed_row = ctk.CTkFrame(versions_frame, fg_color="transparent")
            installed_row.pack(fill="x", pady=(0, 10))
            
            ctk.CTkLabel(
                installed_row,
                text="📥 Installed:",
                font=("Segoe UI", 12),
                text_color=COLORS["subtext"],
                width=120
            ).pack(side="left")
            
            ctk.CTkLabel(
                installed_row,
                text=f"v{pkg['ver']}",
                font=("Segoe UI", 13, "bold"),
                text_color=COLORS["text"]
            ).pack(side="left", padx=(10, 0))
            
            latest_row = ctk.CTkFrame(versions_frame, fg_color="transparent")
            latest_row.pack(fill="x", pady=(0, 10))
            
            ctk.CTkLabel(
                latest_row,
                text="📦 Latest:",
                font=("Segoe UI", 12),
                text_color=COLORS["subtext"],
                width=120
            ).pack(side="left")
            
            latest_label = ctk.CTkLabel(
                latest_row,
                text=pkg['lat'],
                font=("Segoe UI", 13, "bold"),
                text_color=COLORS["accent"]
            )
            latest_label.pack(side="left", padx=(10, 0))
            
            status_frame = ctk.CTkFrame(versions_frame, fg_color="transparent")
            status_frame.pack(fill="x", pady=(10, 0))
            
            ctk.CTkLabel(
                status_frame,
                text="🔄 Status:",
                font=("Segoe UI", 12),
                text_color=COLORS["subtext"],
                width=120
            ).pack(side="left")
            
            status_color = COLORS["success"] if pkg["stat"] == "Updated" else COLORS["warning"] if pkg["stat"] == "Outdated" else COLORS["subtext"]
            dialog_status_label = ctk.CTkLabel(
                status_frame,
                text=pkg['stat'],
                font=("Segoe UI", 13, "bold"),
                text_color=status_color
            )
            dialog_status_label.pack(side="left", padx=(10, 0))
            
            is_outdated = pkg["stat"] == "Outdated"
            is_unknown = pkg["stat"] == "Unknown"
            
            check_now_button = None
            
            if is_unknown:
                check_now_btn = ctk.CTkButton(
                    status_frame,
                    text="Check Now 🔄 ",
                    width=100,
                    height=30,
                    fg_color=COLORS["accent"],
                    hover_color=COLORS["accent_hover"],
                    font=("Segoe UI", 11, "bold"),
                    command=lambda: self._check_single_package_from_dialog(pkg['name'], dialog, dialog_status_label, latest_label, check_now_btn)
                )
                check_now_btn.pack(side="right")
                check_now_button = check_now_btn
            
            ctk.CTkFrame(main_frame, fg_color=COLORS["border"], height=1).pack(fill="x", padx=20, pady=(0, 25))
            
            buttons_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
            buttons_frame.pack(fill="x", padx=20, pady=(0, 20))
            
            CRITICAL_PACKAGES = ["pip", "setuptools", "customtkinter", "tkinter", "wheel"]
            is_critical = pkg["name"].lower() in CRITICAL_PACKAGES
            
            is_updated = pkg["stat"] == "Updated"
            
            if is_outdated or is_updated:
                btn_text = "Update Now 🔄" if is_outdated else "Up to Date ✅"
                btn_state = "normal" if is_outdated else "disabled"
                btn_fg_color = COLORS["success"] if is_outdated else COLORS["surface"]
                btn_hover_color = COLORS["accent_hover"] if is_outdated else COLORS["card"]
                btn_text_color = COLORS["text"] if is_outdated else COLORS["subtext"]
                btn_command = (lambda: [dialog.destroy(), self._confirm_package_update(pkg)]) if is_outdated else None
                
                update_btn = ctk.CTkButton(
                    buttons_frame,
                    text=btn_text,
                    width=400,
                    height=50,
                    fg_color=btn_fg_color,
                    hover_color=btn_hover_color,
                    state=btn_state,
                    text_color=btn_text_color,
                    font=("Segoe UI", 14, "bold"),
                    corner_radius=8,
                    command=btn_command
                )
                update_btn.pack(pady=(0, 12))
            
            deps_btn = ctk.CTkButton(
                buttons_frame,
                text="View Dependencies",
                width=400,
                height=45,
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["text"],
                font=("Segoe UI", 13, "bold"),
                corner_radius=8,
                command=lambda: [dialog.destroy(), self._show_dependencies(pkg['name'])]
            )
            deps_btn.pack(pady=(0, 12))
            
            uninstall_text = "System Package ⚠️ " if is_critical else "Uninstall 🗑️"
            uninstall_btn = ctk.CTkButton(
                buttons_frame,
                text=uninstall_text,
                width=400,
                height=45,
                fg_color=COLORS["danger"] if not is_critical else "#555555",
                hover_color="#d32f2f" if not is_critical else "#666666",
                text_color=COLORS["text"],
                font=("Segoe UI", 13),
                corner_radius=8,
                command=lambda: [dialog.destroy(), self._confirm_uninstall(pkg['name'], is_critical)]
            )
            uninstall_btn.pack(pady=(0, 12))
            
            close_btn = ctk.CTkButton(
                buttons_frame,
                text="✕ Close",
                width=400,
                height=40,
                fg_color=COLORS["card"],
                hover_color=COLORS["border"],
                text_color=COLORS["text"],
                font=("Segoe UI", 12),
                corner_radius=8,
                command=dialog.destroy
            )
            close_btn.pack()
            
            dialog.bind("<Escape>", lambda e: dialog.destroy())
            dialog.focus_set()
            
            dialog.dialog_status_label = dialog_status_label
            dialog.latest_label = latest_label
            dialog.check_now_button = check_now_button
            dialog.package_name = pkg['name']
            
        except Exception as e:
            logger.error(f"Error showing improved action dialog: {e}")
    
    def _check_single_package_from_dialog(self, pkg_name, dialog, status_label, latest_label, check_button):
        """Handle Check Now button click from package dialog."""
        if not dialog or not dialog.winfo_exists():
            return
        
        if check_button:
            check_button.configure(
                state="disabled",
                text="Checking...",
                fg_color=COLORS["surface"]
            )
        
        if status_label:
            status_label.configure(
                text="Checking...",
                text_color=COLORS["accent"]
            )
        
        def update_dialog_callback(success, error_msg):
            self.after(0, lambda: self._update_dialog_after_check(
                pkg_name, dialog, status_label, latest_label, check_button, success, error_msg
            ))
        
        self.core.check_single_package(pkg_name, update_dialog_callback)
    
    def _update_dialog_after_check(self, pkg_name, dialog, status_label, latest_label, check_button, success, error_msg):
        """Update dialog after single package check completes."""
        if not dialog or not dialog.winfo_exists():
            return
        
        try:
            if success:
                updated_pkg = self.core.get_package_by_name(pkg_name)
                
                if updated_pkg:
                    status_color = COLORS["success"] if updated_pkg["stat"] == "Updated" else COLORS["warning"] if updated_pkg["stat"] == "Outdated" else COLORS["subtext"]
                    if status_label:
                        status_label.configure(
                            text=updated_pkg["stat"],
                            text_color=status_color
                        )
                    
                    if latest_label:
                        latest_label.configure(
                            text=updated_pkg['lat']
                        )
                    
                    if updated_pkg["stat"] != "Unknown" and check_button:
                        check_button.pack_forget()
                    
                    self._update_single_package_immediately(pkg_name)
                    
                    logger.info(f"Successfully checked package {pkg_name}: {updated_pkg['stat']}")
                else:
                    if status_label:
                        status_label.configure(
                            text="Check Failed",
                            text_color=COLORS["danger"]
                        )
                    
                    if check_button:
                        check_button.configure(
                            state="normal",
                            text="Try Again",
                            fg_color=COLORS["accent"]
                        )
                    
                    logger.error(f"Failed to get updated info for {pkg_name}")
            
            else:
                if status_label:
                    status_label.configure(
                        text=f"Error: {error_msg[:30]}" if error_msg else "Check Failed",
                        text_color=COLORS["danger"]
                    )
                
                if check_button:
                    check_button.configure(
                        state="normal",
                        text="Try Again",
                        fg_color=COLORS["accent"]
                    )
                
                logger.error(f"Failed to check package {pkg_name}: {error_msg}")
                
        except Exception as e:
            logger.error(f"Error updating dialog after check: {e}")
            if check_button:
                check_button.configure(
                    state="normal",
                    text="Check Now",
                    fg_color=COLORS["accent"]
                )
    
    def _confirm_uninstall(self, pkg_name, is_critical):
        """Confirm package uninstallation."""
        if is_critical:
            title = "CRITICAL WARNING"
            msg = f"'{pkg_name}' is a core system package.\n\nUninstalling may break your environment!"
        else:
            title = "Confirm Uninstall"
            msg = f"Uninstall {pkg_name}?\n\nEnvironment: {self.env_manager.get_current_display()}"

        def response_handler(choice):
            if choice == "Uninstall":
                try:
                    progress_dialog = ProgressDialog(
                        self, pkg_name, None, "uninstall",
                        environment_info=self.env_manager.current_env
                    )
                    
                    self._active_dialogs[pkg_name] = progress_dialog
                    
                    def progress_callback(data):
                        if progress_dialog and progress_dialog.winfo_exists():
                            progress_dialog.update_progress(data)
                    
                    def uninstall_task():
                        try:
                            pip_cmd = self.env_manager.get_pip_command()
                            
                            success, message, _ = run_pip_with_real_progress(
                                ["uninstall", "-y", pkg_name],
                                progress_callback=progress_callback,
                                pip_cmd=pip_cmd
                            )
                            
                            if success:
                                if self._current_env_id:
                                    self.core.save_packages_to_cache(self._current_env_id)
                                
                                self.after(0, lambda: [
                                    progress_dialog.set_completed(True, f"'{pkg_name}' uninstalled."),
                                    self._load_packages(),
                                    self.after(2000, lambda: self._cleanup_dialog(pkg_name, progress_dialog))
                                ])
                                
                            else:
                                self.after(0, lambda: [
                                    progress_dialog.set_completed(False, message),
                                    self._cleanup_dialog(pkg_name, progress_dialog)
                                ])
                        except Exception as e:
                            self.after(0, lambda: [
                                progress_dialog.set_completed(False, str(e)),
                                self._cleanup_dialog(pkg_name, progress_dialog)
                            ])
                    
                    threading.Thread(target=uninstall_task, daemon=True).start()
                except Exception as e:
                    logger.error(f"Error creating uninstall dialog: {e}")
        
        try:
            MessageBox(
                self, title, msg,
                buttons=["Uninstall", "Cancel"], callback=response_handler
            )
        except Exception as e:
            logger.error(f"Error showing uninstall confirmation: {e}")
    
    def _show_dependencies(self, pkg_name):
        """Show package dependencies window."""
        def fetch_and_show():
            try:
                from importlib.metadata import distributions
                dependencies = []
                
                for dist in distributions():
                    current_name = dist.metadata.get("Name") or dist.name
                    if current_name and current_name.lower() == pkg_name.lower():
                        requires = dist.metadata.get_all("Requires-Dist") or []
                        for req in requires:
                            clean = req.split(";")[0].strip().split("(")[0].strip()
                            if clean:
                                dependencies.append(clean)
                        break
                
                dependencies = sorted(set(dependencies))
                self.after(0, lambda: show_dependencies(self, pkg_name, dependencies))
                
            except Exception as e:
                logger.error(f"Error fetching dependencies: {e}")
                self.after(0, lambda: MessageBox.show_error(self, "Error", f"Failed to fetch dependencies: {e}"))
        
        self.thread_manager.submit_task(fetch_and_show, "show_dependencies")


if __name__ == "__main__":
    try:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        app = PackageManagerApp()
        app.mainloop()
    except Exception as e:
        print(f"Fatal error: {e}")
        print(traceback.format_exc())
        sys.exit(1)