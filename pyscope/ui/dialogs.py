import sys
import threading
import time
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPlainTextEdit,
    QDialogButtonBox, QWidget, QHBoxLayout, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QFrame, QFormLayout,
    QMessageBox, QStyledItemDelegate, QStyle
)
from PySide6.QtCore import Qt, QTimer, Slot, Signal, QThread, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QIcon

from ..utils import logger

# --- Workers ---

class GenericWorker(QThread):
    """Universal worker for background tasks"""
    finished = Signal(object)
    error = Signal(str)
    
    def __init__(self, target, *args, **kwargs):
        super().__init__()
        self.target = target
        self.args = args
        self.kwargs = kwargs
        
    def run(self):
        try:
            result = self.target(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

class SearchWorker(QThread):
    results_found = Signal(list)
    error_occurred = Signal(str)
    finished = Signal()
    
    def __init__(self, core, term):
        super().__init__()
        self.core = core
        self.term = term
        
    def run(self):
        try:
            json_results = self.core._search_json_api(self.term)
            results = json_results if json_results else self.core._search_web_scrape(self.term)
            processed = self.core._process_search_results(results)
            self.results_found.emit(processed)
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            self.finished.emit()

# --- Delegates ---

class FastItemDelegate(QStyledItemDelegate):
    """Improved delegate with better spacing and high-DPI support"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_bg = QColor("#00a8c8")
        self.hover_bg = QColor("#1e1e1e")
        self.text_normal = QColor("#e0e0e0")
        self.text_updated = QColor("#4caf50")
        self.text_outdated = QColor("#ff9800")
        self.text_unknown = QColor("#888888")
        self.text_selected = Qt.black
    
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw Background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, self.selected_bg)
            text_color = self.text_selected
        else:
            if option.state & QStyle.State_MouseOver:
                painter.fillRect(option.rect, self.hover_bg)
            
            # Text Coloring logic
            if index.column() == 3:
                status = str(index.data(Qt.DisplayRole)).lower()
                if "updated" in status: text_color = self.text_updated
                elif "outdated" in status: text_color = self.text_outdated
                else: text_color = self.text_unknown
            else:
                text_color = self.text_normal
                
        # Draw Text
        painter.setPen(text_color)
        text = str(index.data(Qt.DisplayRole))
        
        # Padding and vertical centering
        text_rect = option.rect.adjusted(10, 0, -10, 0)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()
    
    def sizeHint(self, option, index):
        return QSize(-1, 32)

# --- Dialogs ---

class ProgressDialog(QDialog):
    """Progress dialog for package operations"""
    
    def __init__(self, parent=None, package_name="", action="install", environment_info=None):
        super().__init__(parent)
        self.package_name = package_name
        self.action = action
        self.environment_info = environment_info
        self.setup_ui()
        self.start_timer()
        
    def setup_ui(self):
        env_text = ""
        if self.environment_info:
             env_display = self.environment_info.get('display', '').split('(')[0].strip()
             env_text = f" [{env_display}]"

        self.setWindowTitle(f"{self.action.title()} {self.package_name}{env_text}")
        # Set window icon
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.setWindowIcon(QIcon(os.path.join(base_dir, "icons", "Packages.png")))
        self.setFixedSize(600, 450)
        layout = QVBoxLayout(self)
        
        header = QLabel(f"{self.action.title()}ing {self.package_name}")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("Starting...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet("background-color: #0d0d0d; color: #cccccc; border-radius: 6px;")
        layout.addWidget(self.log_text, 1)
        
        button_box = QDialogButtonBox()
        self.cancel_btn = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.close_btn = button_box.addButton("Close", QDialogButtonBox.AcceptRole)
        self.close_btn.hide()
        layout.addWidget(button_box)
        
        self.cancel_btn.clicked.connect(self.reject)
        self.close_btn.clicked.connect(self.accept)

    def start_timer(self):
        self.timer = QTimer(self)
        self.timer.start(1000)

    @Slot(dict)
    def update_progress(self, data):
        msg_type = data.get('type')
        if msg_type == 'progress':
            self.progress_bar.setRange(0, 100)
            percent = data.get('percentage', 0)
            self.progress_bar.setValue(percent)
            speed = data.get('speed', 0)
            eta = data.get('eta', '?')
            self.status_label.setText(f"{percent}% - {speed:.1f} MB/s - ETA: {eta}")
        elif msg_type == 'download_start':
            self.progress_bar.setRange(0, 100)
            self.status_label.setText(f"Downloading... {data.get('size_str', '')}")
        elif msg_type == 'installing':
            self.progress_bar.setRange(0, 0)
            self.status_label.setText("Installing...")
        elif msg_type == 'success':
            self.set_completed(True, "Operation successful")
        elif msg_type == 'error':
            self.set_completed(False, data.get('message', 'Unknown Error'))
        if 'line' in data:
            self.log_text.appendPlainText(data['line'])
            
    @Slot(bool, str)
    def set_completed(self, success, message):
        if hasattr(self, 'timer'): self.timer.stop()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        if success:
            self.status_label.setText("✅ Completed successfully!")
            self.log_text.appendPlainText(f"\n✅ {message}")
            self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #4caf50; }")
        else:
            self.status_label.setText(f"❌ Failed: {message}")
            self.log_text.appendPlainText(f"\n❌ {message}")
            self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #f44336; }")
        self.cancel_btn.hide()
        self.close_btn.show()

class SearchDialog(QDialog):
    def __init__(self, parent=None, core=None, on_close=None, current_environment=None):
        super().__init__(parent)
        self.core = core
        self.on_close = on_close
        self.current_environment = current_environment
        self._installed_any = False
        self._current_worker = None
        self.setup_ui()
        
    def setup_ui(self):
        self.setWindowTitle("Install New Packages")
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        if self.current_environment:
             env_lbl = QLabel(f"Installing into: <b>{self.current_environment.get('display', 'Unknown')}</b>")
             env_lbl.setStyleSheet("color: #888888;")
             layout.addWidget(env_lbl)
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type library name...")
        
        # Debounce timer for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.do_search)
        
        self.search_input.textChanged.connect(lambda: self._search_timer.start(300))
        self.search_input.returnPressed.connect(self.do_search)
        
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.do_search)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)
        layout.addLayout(search_layout)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Package", "Version", "Status"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.tree)
        btn_layout = QHBoxLayout()
        self.install_btn = QPushButton("Install Selected")
        self.install_btn.clicked.connect(self.install_selected)
        self.install_btn.setEnabled(False)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)
        self.tree.itemSelectionChanged.connect(lambda: self.install_btn.setEnabled(len(self.tree.selectedItems()) > 0))

    def do_search(self):
        term = self.search_input.text().strip()
        if not term: 
            self.tree.clear()
            return

        # Cancel previous search
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.terminate()
            self._current_worker.wait(100)
        
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Searching...")
        self.tree.clear()
        
        self._current_worker = SearchWorker(self.core, term)
        self._current_worker.results_found.connect(self.on_results)
        self._current_worker.error_occurred.connect(lambda e: QMessageBox.warning(self, "Search Failed", e))
        self._current_worker.finished.connect(lambda: (self.search_btn.setEnabled(True), self.search_btn.setText("Search")))
        self._current_worker.start()

    def on_results(self, results):
        for pkg in results:
            item = QTreeWidgetItem([pkg.get("name", ""), f"v{pkg.get('version', '')}", "Installed" if pkg.get("installed", False) else "Available"])
            if pkg.get("installed", False): item.setForeground(2, QColor("#4caf50"))
            item.setData(0, Qt.UserRole, pkg)
            self.tree.addTopLevelItem(item)

    def install_selected(self):
        items = self.tree.selectedItems()
        if not items: return
        pkg = items[0].data(0, Qt.UserRole)
        progress = ProgressDialog(self, pkg['name'], "install", self.current_environment)
        if hasattr(self.parent(), 'active_dialogs'):
            self.parent().active_dialogs['operation'] = progress
        progress.show()
        self._installed_any = True
        self.core.install_pypi_package(pkg['name'], pkg.get('version'))

    def reject(self):
        """Override reject to return Accepted if something was installed"""
        if self._installed_any:
            self.accept()
        else:
            super().reject()

class PackageDetailsDialog(QDialog):
    # Signal to bridge background thread -> main thread
    update_finished = Signal(bool, str)
    dependencies_loaded = Signal(list)

    def __init__(self, parent=None, package_info=None, core=None, env_manager=None):
        super().__init__(parent)
        self.package_name = package_info['name']
        self.core = core
        self.env_manager = env_manager
        self._alive = True
        self._cancel_event = threading.Event()
        
        self.setup_ui()
        
        # Connect signals
        self.update_finished.connect(self._on_status_checked)
        if self.core and hasattr(self.core, 'signals'):
            self.core.signals.package_updated.connect(self.on_global_update)
            
        # Initial display data
        self.refresh_display()
    
    def on_global_update(self, pkg_name):
        """Respond to updates from anywhere in the app"""
        if pkg_name == self.package_name:
            self.refresh_display()
            
    def refresh_display(self):
        """Fetch fresh data from core and update labels"""
        pkg = self.core.get_package_by_name(self.package_name)
        if not pkg: return
        
        self.package_info = pkg # Sync local copy
        
        # Update labels
        self.version_lbl.setText(f"v{pkg['ver']}")
        self.latest_lbl.setText(pkg['lat'])
        self.status_lbl.setText(pkg['stat'])
        
        # Update colors
        if pkg['stat'] == "Updated": 
            self.status_lbl.setStyleSheet("color: #4caf50;")
        elif pkg['stat'] == "Outdated": 
            self.status_lbl.setStyleSheet("color: #ff9800;")
            if hasattr(self, 'update_btn'): self.update_btn.show()
        else:
            self.status_lbl.setStyleSheet("color: #888888;")

    def closeEvent(self, event):
        """Handle dialog closing"""
        self._alive = False
        self._cancel_event.set()
        
        try:
            if self.core and hasattr(self.core, 'signals'):
                self.core.signals.package_updated.disconnect(self.on_global_update)
        except: pass
        
        event.accept()
    
    def setup_ui(self):
        from PySide6.QtWidgets import QGridLayout
        
        self.setWindowTitle(f"{self.package_name}")
        # Set window icon
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.setWindowIcon(QIcon(os.path.join(base_dir, "icons", "Packages.png")))
        self.setMinimumSize(700, 350) # Increased width for better layout w/ Outdated status
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(25, 25, 25, 25)
        
        # Header
        header = QLabel(self.package_name)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #00a8c8; margin-bottom: 5px;")
        layout.addWidget(header)
        
        # Info Card
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame { 
                background-color: #1a1a1a; 
                border: 1px solid #333; 
                border-radius: 8px; 
                padding: 10px;
            } 
            QLabel { border: none; background: transparent; font-size: 14px; }
        """)
        info_layout = QFormLayout(info_frame)
        info_layout.setSpacing(12)
        
        self.version_lbl = QLabel("...")
        self.version_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        
        self.latest_lbl = QLabel("...")
        self.latest_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        
        self.status_lbl = QLabel("...")
        self.status_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        
        info_layout.addRow("Installed:", self.version_lbl)
        info_layout.addRow("Latest:", self.latest_lbl)
        info_layout.addRow("Status:", self.status_lbl)
        
        layout.addWidget(info_frame)
        
        # Buttons Grid (Horizontal Layout)
        button_group = QWidget()
        button_layout = QGridLayout(button_group)
        button_layout.setSpacing(15)
        button_layout.setContentsMargins(0, 10, 0, 0)
        
        # Get icon directory path
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_dir = os.path.join(base_dir, "icons")
        
        # Actions
        self.check_btn = QPushButton(" Check")
        self.check_btn.setIcon(QIcon(os.path.join(icon_dir, "Search.png")))
        self.check_btn.setIconSize(QSize(18, 18))
        self.check_btn.setFixedWidth(140)
        self.check_btn.clicked.connect(self.check_status)
        
        self.deps_btn = QPushButton(" Deps")
        self.deps_btn.setIcon(QIcon(os.path.join(icon_dir, "Dep.png")))
        self.deps_btn.setIconSize(QSize(18, 18))
        self.deps_btn.setFixedWidth(140)
        self.deps_btn.clicked.connect(self.view_dependencies)
        
        self.uninstall_btn = QPushButton(" Uninstall")
        self.uninstall_btn.setIcon(QIcon(os.path.join(icon_dir, "uninstall.png")))
        self.uninstall_btn.setIconSize(QSize(18, 18))
        self.uninstall_btn.setFixedWidth(140)
        self.uninstall_btn.setStyleSheet("background-color: #d32f2f; color: white;")
        self.uninstall_btn.clicked.connect(self.uninstall_package)

        # Update button (shown conditionally)
        self.update_btn = QPushButton(" Update")
        self.update_btn.setIcon(QIcon(os.path.join(icon_dir, "Packages.png")))
        self.update_btn.setIconSize(QSize(18, 18))
        self.update_btn.setFixedWidth(140)
        self.update_btn.setStyleSheet("background-color: #4caf50; color: white;")
        self.update_btn.clicked.connect(lambda: self.done(10))
        self.update_btn.hide()

        # Add to grid
        col = 0
        button_layout.addWidget(self.check_btn, 0, col); col += 1
        button_layout.addWidget(self.update_btn, 0, col); col += 1
        button_layout.addWidget(self.deps_btn, 0, col); col += 1
        button_layout.addWidget(self.uninstall_btn, 0, col); col += 1
        
        button_layout.setColumnStretch(col, 1)
        
        layout.addWidget(button_group)
        layout.addStretch()
        
        # Footer
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)
    
    def check_status(self):
        self.check_btn.setText("Checking...")
        self.check_btn.setEnabled(False)
        logger.info(f"Starting check for {self.package_name}")
        
        self.core.check_single_package(
            self.package_name, 
            lambda s, e: self.update_finished.emit(s, str(e) if e else "")
        )
        QTimer.singleShot(15000, self._check_timeout)

    def _check_timeout(self):
        if not self.check_btn.isEnabled():
            self._on_status_checked(False, "Operation timed out")

    def _on_status_checked(self, success, error_msg):
        self.check_btn.setText("🔍 Check")
        self.check_btn.setEnabled(True)
        
        if success: 
            self.refresh_display()
        else: 
            if "timed out" not in error_msg:
                 QMessageBox.warning(self, "Check Failed", error_msg or "Unknown error")

    def uninstall_package(self):
        if QMessageBox.question(self, "Confirm Uninstall", f"Are you sure you want to uninstall {self.package_name}?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.done(20)

    def view_dependencies(self):
        deps_dialog = QDialog(self)
        deps_dialog.setWindowTitle(f"Dependencies: {self.package_name}")
        deps_dialog.setFixedSize(400, 300)
        dialog_layout = QVBoxLayout(deps_dialog)
        
        search_input = QLineEdit()
        search_input.setPlaceholderText("Filter dependencies...")
        dialog_layout.addWidget(search_input)
        
        loading_lbl = QLabel("Fetching dependencies...")
        loading_lbl.setAlignment(Qt.AlignCenter)
        dialog_layout.addWidget(loading_lbl)
        
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("background-color: #0d0d0d; color: #cccccc; border-radius: 6px;")
        dialog_layout.addWidget(text_edit)
        text_edit.hide()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(deps_dialog.accept)
        dialog_layout.addWidget(close_btn)
        
        self._current_deps = []
        
        def update_display():
            query = search_input.text().strip().lower()
            filtered = [d for d in self._current_deps if query in d.lower()] if query else self._current_deps
            content = "\n".join([f"• {d}" for d in filtered]) if filtered else "No dependencies listed."
            text_edit.setPlainText(content)

        search_input.textChanged.connect(update_display)
        
        def on_deps_loaded(deps):
            if not hasattr(self, '_alive') or not self._alive: return
            self._current_deps = deps
            loading_lbl.hide()
            update_display()
            text_edit.show()
            try: self.dependencies_loaded.disconnect(on_deps_loaded)
            except: pass

        self.dependencies_loaded.connect(on_deps_loaded)

        def load_task():
            try:
                import subprocess
                pip_cmd = self.env_manager.get_pip_command()
                cmd = pip_cmd + ["show", self.package_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding='utf-8', errors='replace')
                
                final_deps = []
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if line.lower().startswith("requires:"):
                            parts = line.split(":", 1)
                            if len(parts) > 1:
                                deps_str = parts[1].strip()
                                if deps_str:
                                    final_deps = sorted([d.strip() for d in deps_str.split(',') if d.strip()])
                            break
                            
                if not self._cancel_event.is_set() and hasattr(self, '_alive') and self._alive:
                    self.dependencies_loaded.emit(final_deps)
            except Exception as e: 
                logger.error(f"Dep fetch error: {e}")
                if hasattr(self, '_alive') and self._alive and not self._cancel_event.is_set():
                    self.dependencies_loaded.emit([])
        
        threading.Thread(target=load_task, daemon=True).start()
        deps_dialog.exec()
