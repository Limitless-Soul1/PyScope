"""
PyScope Entry Point
Handles initialization, checks, and Windows multiprocessing.
"""
import sys
import multiprocessing
import subprocess
import traceback
import os

from PySide6.QtWidgets import QApplication, QMessageBox
import PySide6.QtCore

# Fix multiprocessing spawning on Windows frozen builds
if sys.platform.startswith('win'):
    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        pass

# Patch subprocess.run to hide console windows on Windows
if sys.platform == 'win32':
    _original_subprocess_run = subprocess.run
    
    def _patched_subprocess_run(*args, **kwargs):
        """Hides console window for subprocess calls."""
        if 'startupinfo' not in kwargs:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs['startupinfo'] = startupinfo
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return _original_subprocess_run(*args, **kwargs)
    
    subprocess.run = _patched_subprocess_run

# Resolve base directory (frozen vs source)
if getattr(sys, 'frozen', False):
    current_dir = os.path.dirname(sys.executable)
else:
    current_dir = os.path.dirname(os.path.abspath(__file__))

# Ensure project root is in path
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)


def main():
    """Application entry point."""
    try:
        from pyscope.system import get_detector
        detector = get_detector("PyScope")
        
        # Health Check
        detector.logger.info("Performing system health check...")
        health = detector.check_health()
        
        # Init Application
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("PyScope")
        app.setApplicationDisplayName("PyScope Package Manager")
        
        # Handle Critical Failures
        if health["status"] == "CRITICAL":
            error_details = []
            if not health["details"].get("python_valid"):
                error_details.append("❌ No valid Python interpreter found")
            if not health["details"].get("pip_available"):
                error_details.append("❌ pip is not available")
            if health["details"].get("python_error"):
                error_details.append(f"Error: {health['details']['python_error']}")
            
            error_msg = "\n".join(error_details) if error_details else "Unknown critical error"
            detector.logger.critical(f"Health check FAILED:\n{error_msg}")
            
            QMessageBox.critical(
                None, "PyScope - Critical Error",
                f"System check failed:\n\n{error_msg}\nCheck logs for details."
            )
            sys.exit(1)
        
        # Load Theme
        from pyscope.ui import QtTheme
        theme = QtTheme()
        theme.apply_to_app(app)
        
        # Launch GUI
        detector.logger.info("Launching Main Window")
        from pyscope.ui import MainWindow
        window = MainWindow()
        window.show()
        
        sys.exit(app.exec())
        
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        if QApplication.instance():
             QMessageBox.critical(None, "PyScope - Fatal Error", f"App failed to start:\n\n{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()