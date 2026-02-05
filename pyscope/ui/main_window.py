import sys
import shiboken6
import hashlib
import time
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox,
    QProgressBar, QStatusBar, QMessageBox, QTreeView,
    QAbstractItemView, QHeaderView, QDialog, QListView
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QSortFilterProxyModel, QRegularExpression, QMetaObject, Slot, QSize
from PySide6.QtGui import QFont, QColor, QPalette, QStandardItemModel, QStandardItem, QIcon

from ..core import PackageManagerCore
from ..environments import EnvironmentManager
from ..utils import logger
from .dialogs import FastItemDelegate, ProgressDialog, SearchDialog, PackageDetailsDialog, GenericWorker

# --- Signals ---

class CoreSignals(QObject):
    """Signals for communication between core logic and UI"""
    # Operation signals
    operation_started = Signal(str, str) # action, package_name
    operation_progress = Signal(dict)
    operation_completed = Signal(bool, str) # success, message
    
    # Update check signals
    check_started = Signal()
    package_updated = Signal(str) # package name
    check_finished = Signal()
    
    # Error signals
    error_occurred = Signal(str)

class EnvironmentSignals(QObject):
    """Signals for environment management"""
    environment_changed = Signal(str) # env_id
    environments_refreshed = Signal(list)

class PackageFilterProxy(QSortFilterProxyModel):
    """Advanced proxy model for high-performance searching and status filtering"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_mode = "All"
        self.search_term = ""

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model: return True
        
        # 1. Search Filter (Column 0: Name)
        if self.search_term:
            name = model.item(source_row, 0).text().lower()
            if self.search_term not in name:
                return False
                
        # 2. Status Filter (Check data in model instead of text for robustness)
        if self.filter_mode == "All":
            return True
            
        item = model.item(source_row, 0)
        pkg_data = item.data(Qt.UserRole)
        if not pkg_data: return True
        
        status = pkg_data.get("stat", "Unknown")
        if self.filter_mode == "Updated" and status != "Updated": return False
        if self.filter_mode == "Outdated" and status != "Outdated": return False
        
        return True

# --- Theme ---

class QtTheme(QObject):
    """Manages Qt application theme"""
    
    theme_changed = Signal()
    
    def __init__(self):
        super().__init__()
        self.COLORS = {
            "bg": "#0d0d0d",  # Slightly lighter/warmer than pure black
            "surface": "#141414",
            "card": "#1e1e1e",
            "accent": "#00a8c8", # More vibrant cyan
            "accent_hover": "#00bcd4",
            "text": "#e0e0e0", # Softer white
            "subtext": "#888888",
            "success": "#4caf50",
            "warning": "#ff9800",
            "danger": "#f44336",
            "border": "#333333", # Higher contrast border
            "progress_bg": "#2a2a2a",
            "progress_fg": "#00a8c8"
        }
    
    def apply_to_app(self, app):
        """Apply theme to QApplication instance"""
        from PySide6.QtWidgets import QStyleFactory
        if "Fusion" in QStyleFactory.keys():
            app.setStyle(QStyleFactory.create("Fusion"))
        
        self.apply_palette(app)
        self.apply_stylesheet(app)
        app.setFont(QFont("Segoe UI", 10))
        
    def apply_palette(self, app):
        """Apply color palette to application"""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(self.COLORS["bg"]))
        palette.setColor(QPalette.WindowText, QColor(self.COLORS["text"]))
        palette.setColor(QPalette.Base, QColor(self.COLORS["card"]))
        palette.setColor(QPalette.AlternateBase, QColor(self.COLORS["surface"]))
        palette.setColor(QPalette.ToolTipBase, QColor(self.COLORS["accent"]))
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, QColor(self.COLORS["text"]))
        palette.setColor(QPalette.Button, QColor(self.COLORS["surface"]))
        palette.setColor(QPalette.ButtonText, QColor(self.COLORS["text"]))
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(self.COLORS["accent"]))
        palette.setColor(QPalette.Highlight, QColor(self.COLORS["accent"]))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        palette.setColor(QPalette.Disabled, QPalette.Text, QColor(self.COLORS["subtext"]))
        palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(self.COLORS["subtext"]))
        app.setPalette(palette)

    def apply_stylesheet(self, app):
        """Apply CSS stylesheet"""
        stylesheet = f"""
        QMainWindow {{ background-color: {self.COLORS["bg"]}; }}
        QWidget {{ color: {self.COLORS["text"]}; font-family: 'Segoe UI'; }}
        
        /* Buttons */
        QPushButton {{ 
            background-color: {self.COLORS["accent"]}; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 6px; 
            font-weight: bold; 
            font-size: 13px;
        }}
        QPushButton:hover {{ background-color: {self.COLORS["accent_hover"]}; }}
        QPushButton:pressed {{ background-color: #00838f; }}
        QPushButton:disabled {{ background-color: {self.COLORS["surface"]}; color: {self.COLORS["subtext"]}; border: 1px solid {self.COLORS["border"]}; }}
        
        /* Inputs */
        QLineEdit {{ 
            background-color: {self.COLORS["card"]}; 
            color: white; 
            border: 1px solid {self.COLORS["border"]}; 
            border-radius: 6px; 
            padding: 8px; 
            selection-background-color: {self.COLORS["accent"]}; 
        }}
        QLineEdit:focus {{ border: 1px solid {self.COLORS["accent"]}; }}
        
        QComboBox {{ 
            background-color: {self.COLORS["card"]}; 
            color: white; 
            border: 1px solid {self.COLORS["border"]}; 
            border-radius: 8px; 
            padding: 8px 12px; 
            min-height: 28px;
            font-size: 12px;
        }}
        QComboBox:hover {{ border: 1px solid {self.COLORS["accent"]}; }}
        QComboBox:on {{ border: 1px solid {self.COLORS["accent"]}; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }}
        QComboBox::drop-down {{ 
            border: none; 
            width: 30px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 5px solid {self.COLORS["subtext"]};
            margin-right: 10px;
        }}
        QComboBox::down-arrow:hover {{ border-top-color: {self.COLORS["accent"]}; }}
        
        QComboBox QAbstractItemView {{
            background-color: {self.COLORS["surface"]};
            color: {self.COLORS["text"]};
            selection-background-color: {self.COLORS["accent"]};
            selection-color: black;
            border: 1px solid {self.COLORS["accent"]};
            border-top: none;
            outline: none;
            padding: 4px;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 45px;
            padding-left: 15px;
            border-bottom: 1px solid {self.COLORS["border"]};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {self.COLORS["accent"]};
            color: black;
            border-radius: 4px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: rgba(0, 168, 200, 0.15);
            color: {self.COLORS["accent"]};
        }}
        
        /* Specialized Refresh Button */
        #env_refresh_btn {{
            background-color: {self.COLORS["surface"]};
            border: 1px solid {self.COLORS["border"]};
            color: {self.COLORS["accent"]};
            font-size: 16px;
            padding: 0px;
            border-radius: 6px;
        }}
        #env_refresh_btn:hover {{
            border-color: {self.COLORS["accent"]};
            background-color: {self.COLORS["card"]};
        }}
        
        /* Lists & Trees */
        QTreeWidget, QTreeView, QListWidget {{ 
            background-color: {self.COLORS["surface"]}; 
            alternate-background-color: {self.COLORS["card"]}; 
            color: {self.COLORS["text"]}; 
            border: 1px solid {self.COLORS["border"]}; 
            border-radius: 6px; 
            outline: none;
        }}
        QHeaderView::section {{ 
            background-color: {self.COLORS["bg"]}; 
            color: {self.COLORS["subtext"]}; 
            padding: 10px; 
            border: none; 
            font-weight: bold; 
            text-transform: uppercase;
            font-size: 11px;
        }}
        
        /* Progress & Status */
        QProgressBar {{ 
            border: 1px solid {self.COLORS["border"]}; 
            border-radius: 4px; 
            text-align: center; 
            color: white; 
            background-color: {self.COLORS["progress_bg"]}; 
        }}
        QProgressBar::chunk {{ background-color: {self.COLORS["progress_fg"]}; border-radius: 3px; }}
        QStatusBar {{ background-color: {self.COLORS["surface"]}; color: {self.COLORS["subtext"]}; border-top: 1px solid {self.COLORS["border"]}; }}
        
        /* Text Areas */
        QPlainTextEdit {{ 
            background-color: {self.COLORS["card"]}; 
            color: {self.COLORS["text"]}; 
            border: 1px solid {self.COLORS["border"]}; 
            border-radius: 6px; 
        }}
        
        /* Scrollbars (Subtle) */
        QScrollBar:vertical {{ border: none; background: {self.COLORS["bg"]}; width: 10px; margin: 0px; }}
        QScrollBar::handle:vertical {{ background: #333; min-height: 20px; border-radius: 5px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """
        app.setStyleSheet(stylesheet)

# --- Main Window ---

class MainWindow(QMainWindow):
    """Main application window for PyScope"""
    
    package_loaded = Signal()
    status_changed = Signal(str)
    
    def __init__(self):
        super().__init__()
        self.theme = QtTheme()
        self.core = PackageManagerCore()
        self.env_manager = EnvironmentManager()
        self.current_env_id = self._get_environment_id()
        self.core.set_pip_command(self.env_manager.get_pip_command())
        self.update_in_progress = False
        self.active_dialogs = {}
        self.pending_updates = []
        self.filter_buttons = {}
        self._load_session = 0
        self._refresh_pending = False
        self.setup_window()
        self.core.signals = CoreSignals()
        
        # Search debounce
        self._search_debounce_timer = QTimer()
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self._apply_search_to_proxy)
        
        # UI state generation
        self._ui_generation = 0
        
        self.setup_ui()
        self.setup_signals()
        QTimer.singleShot(500, self.load_packages)

    def closeEvent(self, event):
        """Handle application shutdown"""
        logger.info("Application closing...")
        try:
            # Stop auto check timer
            if hasattr(self, 'auto_check_timer'):
                self.auto_check_timer.stop()
            
            # Shutdown core
            if self.core:
                self.core.shutdown()
                
            # Allow event to propagate
            event.accept()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            event.accept()

    def setup_window(self):
        self.setWindowTitle("PyScope - Modern Python Package Manager")
        self.resize(1100, 750)
        self.setMinimumSize(900, 600)
        
        # Set window icon for taskbar and window frame
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_path = os.path.join(base_dir, "icons", "logo.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _get_environment_id(self):
        path = self.env_manager.get_python_command()
        return hashlib.md5(path.encode()).hexdigest()

    def setup_ui(self):
        # Add auto-check timer
        self.auto_check_timer = QTimer()
        self.auto_check_timer.setSingleShot(True)
        self.auto_check_timer.timeout.connect(self._auto_check_loaded)
        self.auto_check_timer.start(2000)  # 2 seconds check

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)
        
        env_label = QLabel("Python Environment:")
        env_label.setStyleSheet(f"color: {self.theme.COLORS['accent']}; font-weight: bold; font-size: 12px;")
        top_bar.addWidget(env_label)
        
        self.environment_combo = QComboBox()
        self.environment_combo.setFixedWidth(320)
        self.environment_combo.setView(QListView())  # Use QListView for professional popup
        self.environment_combo.setStyleSheet("""
            QComboBox {
                background-color: #252525;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 5px 10px;
                padding-right: 30px;
                min-width: 300px;
            }
            QComboBox:hover {
                border: 1px solid #00bcd4;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #404040;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                background: #252525;
            }
            QComboBox::down-arrow {
                width: 0; 
                height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #e0e0e0;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #404040;
                selection-background-color: #00bcd4;
                selection-color: white;
                outline: none;
                padding: 5px;
            }
        """)
        self.environment_combo.currentIndexChanged.connect(self.on_environment_changed)
        top_bar.addWidget(self.environment_combo)
        
        # Get the base directory (where this file is located, go up 2 levels to reach project root)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_dir = os.path.join(base_dir, "icons")
        
        self.refresh_envs_btn = QPushButton()
        self.refresh_envs_btn.setIcon(QIcon(os.path.join(icon_dir, "refresh.png")))
        self.refresh_envs_btn.setIconSize(QSize(24, 24))
        self.refresh_envs_btn.setObjectName("env_refresh_btn")
        self.refresh_envs_btn.setFixedSize(40, 40)
        self.refresh_envs_btn.setToolTip("Refresh Environments")
        self.refresh_envs_btn.clicked.connect(self.refresh_environments)
        top_bar.addWidget(self.refresh_envs_btn)
        top_bar.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search installed packages...")
        self.search_input.setFixedWidth(250)
        # Use debounce instead of immediate refresh for search
        self.search_input.textChanged.connect(lambda: self._search_debounce_timer.start(200))
        top_bar.addWidget(self.search_input)
        self.add_btn = QPushButton(" Install Package")
        self.add_btn.setIcon(QIcon(os.path.join(icon_dir, "Add.png")))
        self.add_btn.setIconSize(QSize(20, 20))
        self.add_btn.clicked.connect(self.open_search)
        top_bar.addWidget(self.add_btn)
        layout.addLayout(top_bar)
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)
        modes = ["All", "Updated", "Outdated"]
        for mode in modes:
            btn = QPushButton(mode)
            btn.setCheckable(True)
            if mode == "All": btn.setChecked(True)
            # Fix: Handle the boolean argument from 'clicked' signal so it doesn't override 'm'
            btn.clicked.connect(lambda _, m=mode: self.apply_filter(m))
            self.filter_buttons[mode] = btn
            filter_bar.addWidget(btn)
        
        filter_bar.addStretch()
        self.stats_label = QLabel("Loading packages...")
        self.stats_label.setStyleSheet("color: #888888; font-weight: bold;")
        filter_bar.addWidget(self.stats_label)
        self.check_btn = QPushButton(" Check for Updates")
        self.check_btn.setIcon(QIcon(os.path.join(icon_dir, "Search.png")))
        self.check_btn.setIconSize(QSize(20, 20))
        self.check_btn.clicked.connect(self.check_updates)
        filter_bar.addWidget(self.check_btn)
        layout.addLayout(filter_bar)
        self.view, self.model, self.proxy = self.create_package_view()
        layout.addWidget(self.view, 1)
        self.progress_section = QWidget()
        self.progress_section.hide()
        prog_layout = QVBoxLayout(self.progress_section)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        prog_layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_section)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        self.status_bar.showMessage("Ready")
        self.status_bar.showMessage("Ready")
        self.refresh_environments()
        
        # Initialize styles and filter (Must be called after all UI elements are created)
        self.apply_filter("All")

    def _auto_check_loaded(self):
        """Check if packages are loaded and retry if needed."""
        packages, total, outdated = self.core.refresh_packages_data()
        
        if total == 0 and not hasattr(self, '_auto_retry_count'):
            self._auto_retry_count = 1
            logger.warning("⚠️ No packages detected, auto-retrying...")
            self.load_packages()
        elif total > 0 and not hasattr(self, '_ui_initialized'):
            self._ui_initialized = True
            logger.info(f"✅ Auto-detected {total} packages, refreshing UI...")
            self.refresh_tree()

    def create_package_view(self):
        view = QTreeView()
        view.setAlternatingRowColors(True)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setSelectionMode(QAbstractItemView.SingleSelection)
        view.setItemDelegate(FastItemDelegate(view))
        
        model = QStandardItemModel(0, 4)
        model.setHorizontalHeaderLabels(["Package", "Version", "Latest", "Status"])
        
        # Use proxy model for filtering
        proxy = PackageFilterProxy(self)
        proxy.setSourceModel(model)
        
        view.setModel(proxy)
        view.header().setSectionResizeMode(0, QHeaderView.Stretch)
        view.header().setStretchLastSection(False)
        view.setColumnWidth(1, 100)
        view.setColumnWidth(2, 100)
        view.setColumnWidth(3, 150)
        view.doubleClicked.connect(self.on_package_double_clicked)
        return view, model, proxy

    def setup_signals(self):
        self.core.signals.check_started.connect(lambda: (self.progress_bar.setRange(0, 0), self.progress_bar.setValue(0)))
        self.core.signals.package_updated.connect(self.on_package_checked)
        self.core.signals.check_finished.connect(self.on_update_check_finished)
        self.core.signals.operation_progress.connect(self.on_operation_progress)
        self.core.signals.operation_completed.connect(self.on_operation_completed)
        # Connect explicit package loaded signal for robust threading
        self.package_loaded.connect(self.on_packages_loaded)
        
    def load_packages(self, force_refresh: bool = False):
        """Load packages with automatic UI refresh (Signal-based)"""
        if hasattr(self, 'worker') and self.worker and self.worker.isRunning():
            logger.debug("Worker already running, skipping")
            return
        
        # Show loading status without clearing the model
        self.status_bar.showMessage("🔍 Loading packages...")
        
        # Create callback ensuring UI update via Signal (Thread-Safe)
        def ui_callback():
            self.package_loaded.emit()
        
        # Load packages with callback
        self.worker = GenericWorker(
            self.core.load_packages_with_cache, 
            ui_callback=ui_callback, 
            environment_id=self.current_env_id,
            force_refresh=force_refresh
        )
        self.worker.finished.connect(lambda x: None)
        self.worker.start()

    @Slot()
    def on_packages_loaded(self):
        """Called when packages are loaded - Runs in Main Thread via Signal"""
        self.status_bar.showMessage("✓ Packages loaded successfully")
        
        # Use debounced refresh to update UI
        self.refresh_tree()

    def refresh_tree(self):
        """Debounced refresh to avoid excessive UI updates and ensure logic consolidation"""
        if self._refresh_pending:
            return
        
        self._refresh_pending = True
        QTimer.singleShot(50, self._do_refresh_tree)
        
    def _do_do_refresh_stats(self):
        """Update stats label with latest core data"""
        packages, total, outdated = self.core.refresh_packages_data()
        self.stats_label.setText(f"Packages: {total} | Updates: {outdated} | {self.env_manager.get_current_display()}")

    def _do_refresh_tree(self):
        """Actually perform the tree refresh"""
        self._refresh_pending = False
        self._do_do_refresh_stats()
        
        # Update model in UI thread
        self._update_tree_model()
        
    def _update_tree_model(self):
        """Update tree model efficiently (Single source of truth) - NON-BLOCKING"""
        try:
            # Get latest data from core
            packages, total, outdated = self.core.refresh_packages_data()
            
            # Update Statistics Label
            self.stats_label.setText(f"Packages: {total} | Updates: {outdated} | {self.env_manager.get_current_display()}")
            
            if total == 0:
                if self.model.rowCount() > 0:
                    self.model.removeRows(0, self.model.rowCount())
                self.stats_label.setText("❌ No packages installed")
                return

            current_count = self.model.rowCount()
            new_count = len(packages)
            
            # Incremental update
            if current_count == 0:
                # First load
                self._load_session += 1
                self._load_model_chunk(packages, 0, self._load_session, 50)
            else:
                # Update existing rows first (in chunks)
                self._load_session += 1
                self._update_rows_chunk(packages, 0, self._load_session, 50)
                
                # Then add new rows if needed (incrementally, not rebuild!)
                if new_count > current_count:
                    for i in range(current_count, new_count):
                        row = [QStandardItem(), QStandardItem(), QStandardItem(), QStandardItem()]
                        self.model.appendRow(row)
                        self._set_row_data(self.model.rowCount() - 1, packages[i])
                
                # Or remove extra rows if needed (from the end)
                elif new_count < current_count:
                    self.model.removeRows(new_count, current_count - new_count)

            # Invalidate proxy to trigger re-filtering
            self.proxy.invalidateFilter()
            logger.debug(f"✅ UI updated: {total} packages in model")
            
        except Exception as e:
            logger.error(f"❌ UI Update failed: {e}")
    
    def _update_rows_chunk(self, packages, start_index, session_id, chunk_size=50):
        """Update rows in chunks to avoid UI freeze during refresh"""
        if session_id != self._load_session or start_index >= len(packages):
            return
        end_index = min(start_index + chunk_size, len(packages))
        for i in range(start_index, end_index):
            if i < self.model.rowCount():
                self._set_row_data(i, packages[i])
        if end_index < len(packages):
            QTimer.singleShot(5, lambda: self._update_rows_chunk(packages, end_index, session_id, chunk_size))

    def _set_row_data(self, row_idx, pkg):
        """Efficiently update a single row without rebuilding"""
        # Package Name (Col 0)
        self.model.item(row_idx, 0).setText(pkg["name"])
        self.model.item(row_idx, 0).setData(pkg, Qt.UserRole)
        
        # Version (Col 1)
        self.model.item(row_idx, 1).setText(f"v{pkg['ver']}")
        
        # Latest (Col 2)
        self.model.item(row_idx, 2).setText(pkg["lat"])
        
        # Status (Col 3)
        status = pkg["stat"]
        status_item = self.model.item(row_idx, 3)
        if status == "Updated":
            status_item.setText("✓ Updated")
            status_item.setForeground(QColor("#4caf50"))
        elif status == "Outdated":
            status_item.setText("▲ Outdated")
            status_item.setForeground(QColor("#ff9800"))
        else:
            status_item.setText("[--] Unknown")
            status_item.setForeground(QColor("#888888"))

    def _load_model_chunk(self, items, start_index, session_id, chunk_size=50):
        if session_id != self._load_session or start_index >= len(items): return
        end_index = min(start_index + chunk_size, len(items))
        for i in range(start_index, end_index):
            pkg = items[i]
            row = [QStandardItem(), QStandardItem(), QStandardItem(), QStandardItem()]
            self.model.appendRow(row)
            self._set_row_data(self.model.rowCount() - 1, pkg)
            
        if end_index < len(items): QTimer.singleShot(10, lambda: self._load_model_chunk(items, end_index, session_id, chunk_size))

    def _apply_search_filter(self):
        """Local filtering without reloading from system"""
        # Checks if window/object is valid
        if not self.isVisible() or not shiboken6.isValid(self):
            return
            
        search_term = self.search_input.text().strip().lower()
        filter_mode = next((k for k, v in self.filter_buttons.items() if v.isChecked()), "All")
        
        # Filter local package list (No pipe list)
        filtered = self.core.filter_packages(filter_mode)
        if search_term:
            filtered = [p for p in filtered if search_term in p["name"].lower()]
        
        # Update model directly (Bypassing proxy logic for filtering to ensure state preservation)
        # Note: We must ensure proxy is passing everything if we do this, 
        # OR we just update the source model and let proxy reflect it.
        # But wait, if we update source model with SUBSET, we lose the other packages?
        # User's request implies we are showing "results".
        
        # CLEAR MODEL first
        if self.model.rowCount() > 0:
             self.model.removeRows(0, self.model.rowCount())
        
        # Re-populate incrementally
        session = time.time()
        self._load_session = session # Update session to invalidate old loads
        self._load_model_chunk(filtered, 0, session, 50)
        
        # Ensure proxy doesn't double-filter
        self.proxy.search_term = ""
        self.proxy.filter_mode = "All"
        self.proxy.invalidateFilter()
        
        # Feedback
        count = len(filtered)
        if count == 0 and search_term:
            self.status_bar.showMessage(f"No results for '{search_term}'", 2000)
        elif search_term:
            self.status_bar.showMessage(f"Found {count} packages", 2000)

    def apply_filter(self, mode):
        self.proxy.filter_mode = mode
        for k, v in self.filter_buttons.items():
            is_selected = (k == mode)
            v.setChecked(is_selected)
            if is_selected:
                v.setStyleSheet("""
                    QPushButton {
                        background-color: #00a8c8;
                        color: black;
                        border: none;
                        padding: 6px 18px;
                        border-radius: 6px;
                        font-weight: bold;
                    }
                """)
            else:
                v.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #00a8c8;
                        border: 1px solid #00a8c8;
                        padding: 6px 18px;
                        border-radius: 6px;
                    }
                    QPushButton:hover {
                        background-color: rgba(0, 168, 200, 0.1);
                    }
                """)
        
        # Fast Filtering: Directly invalidate proxy to trigger filter logic immediately
        self.proxy.invalidateFilter()

    def check_updates(self):
        if self.update_in_progress: 
            return
            
        # New Cooldown Logic
        if hasattr(self, '_last_op_time') and time.time() - self._last_op_time < 30:
            remaining = int(30 - (time.time() - self._last_op_time))
            self.status_bar.showMessage(f"⏳ Waiting for PyPI sync ({remaining}s)...", 3000)
            return
    
        # Generation guard
        self._update_check_generation = getattr(self, '_update_check_generation', 0) + 1
        current_gen = self._update_check_generation
        
        # Protective delay
        if hasattr(self, '_last_env_change_time') and time.time() - self._last_env_change_time < 0.5:
            QTimer.singleShot(300, lambda: self._start_update_check(current_gen))
            return
        
        self._start_update_check(current_gen)

    def _start_update_check(self, expected_gen):
        # Validate generation
        if getattr(self, '_update_check_generation', 0) != expected_gen:
            return  # Abandon: this check is obsolete
        
        self.update_in_progress = True
        self.progress_section.show()
        self.progress_bar.setRange(0, 100)
        self.check_btn.setEnabled(False)
        
        # Safe callback wrapper
        def safe_finish():
            if getattr(self, '_update_check_generation', 0) != expected_gen:
                return  # Abandon: stale callback
            QMetaObject.invokeMethod(self, "on_update_check_finished", Qt.QueuedConnection)
            
        def safe_package(pkg_name):
            if getattr(self, '_update_check_generation', 0) != expected_gen:
                return  # Abandon: stale callback
            self.on_package_checked(pkg_name)
        
        self.core.check_updates(
            ui_finish_callback=safe_finish,
            ui_package_callback=safe_package
        )

    def on_package_checked(self, pkg_name):
        pkg = self.core.get_package_by_name(pkg_name)
        if not pkg: return
        for i in range(self.model.rowCount()):
            if self.model.item(i, 0).text() == pkg_name:
                self.model.item(i, 0).setData(pkg, Qt.UserRole)  # Fix: Update data for proxy filter
                self.model.item(i, 2).setText(pkg["lat"])
                if pkg["stat"] == "Outdated":
                    self.model.item(i, 3).setText("▲ Outdated")
                    self.model.item(i, 3).setForeground(QColor("#ff9800"))
                elif pkg["stat"] == "Updated":
                    self.model.item(i, 3).setText("✓ Updated")
                    self.model.item(i, 3).setForeground(QColor("#4caf50"))
                
                # Trigger filter update if needed
                if self.proxy.filter_mode != "All":
                    self.proxy.invalidateFilter()
                break

    @Slot()
    def on_update_check_finished(self):
        self.update_in_progress = False
        self.progress_section.hide()
        self.check_btn.setEnabled(True)
        self.refresh_tree()

    def on_operation_progress(self, data):
        if 'operation' in self.active_dialogs:
            self.active_dialogs['operation'].update_progress(data)

    def on_operation_completed(self, success, message):
        self._last_op_time = time.time()
        pkg_name = None
        action = None
        
        if 'operation' in self.active_dialogs:
            # Get the package name and action BEFORE deleting the dialog reference
            dialog = self.active_dialogs['operation']
            pkg_name = getattr(dialog, 'package_name', None)
            action = getattr(dialog, 'action', None)
            del self.active_dialogs['operation']
        
        if success and pkg_name:
            if action == 'uninstall':
                # FIX: Immediately remove row for uninstall
                for i in range(self.model.rowCount()):
                    if self.model.item(i, 0).text() == pkg_name:
                        self.model.removeRow(i)
                        self.status_bar.showMessage(f"✗ {pkg_name} removed successfully", 5000)
                        # Reset stats
                        self.core.refresh_packages_data() # Update internal counts
                        # Update stats label manually or via signal? 
                        # Ideally refresh_packages_data returns new counts, but we can just trigger a silent background reload
                        break
                self.load_packages(force_refresh=True) # Sync fully in background
            elif action == 'install':
                 # FIX: Skip redundant load_packages for install (handled via _update_after_install)
                 # Just refresh UI after slight delay for file system stability
                 QTimer.singleShot(300, self.refresh_tree)
                 self.status_bar.showMessage(f"✅ {pkg_name} installed successfully", 5000)
            else:
                # Update: Use check_single_package to verify new version
                def on_single_check_done(s, m):
                    self.refresh_tree()
                    self.status_bar.showMessage(f"✓ {pkg_name} updated successfully", 5000)
                
                self.core.check_single_package(pkg_name, callback=on_single_check_done)
                # Also reload packages list in background to ensure consistency
                self.load_packages(force_refresh=True)
        else:
            # Fallback: Full reload for failures or when package name unknown
            self.load_packages(force_refresh=True)
            self.refresh_tree()

    def on_package_double_clicked(self, index):
        # Handle proxy model mapping
        source_index = self.proxy.mapToSource(index)
        pkg = self.model.item(source_index.row(), 0).data(Qt.UserRole)
        
        dialog = PackageDetailsDialog(self, pkg, self.core, self.env_manager)
        res = dialog.exec()
        
        # Handle custom return codes
        if res == 10: 
            self.perform_package_action(pkg['name'], pkg['lat'], "update")
        elif res == 20: 
            self.perform_package_action(pkg['name'], pkg['ver'], "uninstall")
            
        # Refresh tree after any dialog interaction to sync state
        QTimer.singleShot(100, self.refresh_tree)

    def perform_package_action(self, name, ver, action):
        dialog = ProgressDialog(self, name, action, self.env_manager.current_env)
        self.active_dialogs['operation'] = dialog
        dialog.show()
        if action == "uninstall": self.core.uninstall_package(name)
        else: self.core.install_pypi_package(name, ver)

    def open_search(self):
        dlg = SearchDialog(self, self.core)
        dlg.exec()
    
    def _apply_search_to_proxy(self):
        """Apply search filter to proxy model"""
        search_text = self.search_input.text().strip().lower()
        self.proxy.search_term = search_text
        self.proxy.invalidateFilter()
        
        # Update status
        if search_text:
            visible_count = self.proxy.rowCount()
            self.status_bar.showMessage(f"🔍 Found {visible_count} packages matching '{search_text}'", 3000)
        else:
            self.status_bar.showMessage("", 100)

    def refresh_environments(self):
        self.environment_combo.blockSignals(True)
        self.environment_combo.clear()
        self.environment_combo.addItem("Scanning environments...", None)
        self.environment_combo.setEnabled(False)
        
        # Async Refresh to prevent UI freeze
        def on_refresh_finished():
            self.environment_combo.clear()
            envs = self.env_manager.all_environments
            current = self.env_manager.get_python_command()
            
            for i, env in enumerate(envs):
                self.environment_combo.addItem(env['display'], env['python_path'])
                if env['python_path'] == current: 
                    self.environment_combo.setCurrentIndex(i)
            
            self.environment_combo.setEnabled(True)
            self.environment_combo.blockSignals(False)
            
        # Use GenericWorker for async execution
        self.env_worker = GenericWorker(self.env_manager.refresh)
        self.env_worker.finished.connect(lambda _: on_refresh_finished())
        self.env_worker.error.connect(lambda e: [
             self.environment_combo.clear(),
             self.environment_combo.addItem("Error scanning environments", None),
             self.environment_combo.setEnabled(True),
             self.environment_combo.blockSignals(False),
             logger.error(f"Env refresh error: {e}")
        ])
        self.env_worker.start()

    def on_environment_changed(self, index):
        if index < 0 or index >= self.environment_combo.count():
            return
        
        # 1. Immediate Visual Update
        self.environment_combo.blockSignals(True)
        self.environment_combo.setCurrentIndex(index)
        self.environment_combo.blockSignals(False)
        
        self._last_env_change_time = time.time()
        
        # 2. Cancel previous checks and increment operation generation
        self.core.cancel_check()
        
        # Immediately reset UI state if checking was in progress
        if self.update_in_progress:
            self.update_in_progress = False
            self.progress_section.hide()
            self.check_btn.setEnabled(True)
            self.status_bar.showMessage("Update check cancelled", 2000)
        
        self._ui_generation += 1  # Invalidate previous pending UI updates
        current_gen = self._ui_generation
        
        # Cancel active operation to switch environment
        had_active_operation = self.core.is_operation_active()
        
        # Show brief message if an operation was cancelled
        if had_active_operation:
            self.status_bar.showMessage("Cancelling operation to switch environment", 3000)

        path = self.environment_combo.itemData(index)
        # Find env dict by path
        env = next((e for e in self.env_manager.all_environments if e["python_path"] == path), None)
        if env:
            self.env_manager.set_environment(env)
            self.core.set_pip_command(self.env_manager.get_pip_command())
            self.current_env_id = self._get_environment_id()
            
            # 4. Load with safe callback checking generation
            def safe_refresh():
                if self._ui_generation != current_gen:
                    logger.debug(f"Skipping stale UI refresh (gen {current_gen} vs current {self._ui_generation})")
                    return
                # Must invoke execution on Main Thread for UI updates
                QMetaObject.invokeMethod(self, "on_packages_loaded", Qt.QueuedConnection)
            
            # Note: core.py calls callback safely now.
            self.core.load_packages_with_cache(
                ui_callback=safe_refresh,
                environment_id=self.current_env_id
            )
