"""
System Detector
Handles environment detection, path resolution, and system health checks.
"""
import os
import sys
import time
import socket
import platform
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any


def validate_python_executable(path: str) -> bool:
    """Validate that a given path is a working Python interpreter."""
    if not path or not os.path.exists(path):
        return False
    try:
        result = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


class SystemDetector:
    """Detects and manages the application execution environment."""
    
    def __init__(self, app_name: str = "PyScope"):
        self.app_name = app_name
        self.is_frozen = getattr(sys, 'frozen', False)
        self.base_path = self._get_base_path()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = self._setup_logging()
        self._cached_python_path = None
        
    def _get_base_path(self) -> str:
        """Resolve base path for both source and PyInstaller environments."""
        if self.is_frozen:
            return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _setup_logging(self) -> str:
        """Setup minimal logging (Disabled for production)."""
        self.logger = logging.getLogger("PyScope")
        self.logger.setLevel(logging.CRITICAL)  # Only critical errors
        self.logger.handlers = []
        
        class NullHandler(logging.Handler):
            def emit(self, record): pass
        
        self.logger.addHandler(NullHandler())
        return ""

    def get_resource_path(self, relative_path: str) -> str:
        """Get absolute path to a resource."""
        return os.path.join(self.base_path, relative_path)

    def get_actual_python_executable(self) -> str:
        """
        Finds a valid Python interpreter.
        In frozen mode, searches system paths since sys.executable is the app itself.
        """
        if self._cached_python_path:
            return self._cached_python_path
            
        if not self.is_frozen:
            self._cached_python_path = sys.executable
            return self._cached_python_path

        self.logger.info("Searching for valid Python interpreter...")
        
        candidates = []
        
        # 1. Active virtual environments
        if os.environ.get('VIRTUAL_ENV'):
            candidates.append(os.path.join(os.environ['VIRTUAL_ENV'], 'Scripts', 'python.exe'))
        if os.environ.get('CONDA_PREFIX'):
            candidates.append(os.path.join(os.environ['CONDA_PREFIX'], 'python.exe'))
        
        # 2. Local environment
        app_dir = os.path.dirname(sys.executable)
        candidates.extend([
            os.path.join(app_dir, 'python.exe'),
            os.path.join(app_dir, '..', 'python.exe'),
            os.path.join(app_dir, 'Scripts', 'python.exe'),
            os.path.join(app_dir, 'venv', 'Scripts', 'python.exe'),
        ])
        
        # 3. Windows 'py' launcher
        if sys.platform == 'win32':
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                
                result = subprocess.run(
                    ['py', '-0p'],
                    capture_output=True, text=True, timeout=5, shell=False,
                    startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if 'python' in line.lower() or 'Python' in line:
                            parts = line.split()
                            for part in parts:
                                if 'python' in part.lower() and os.path.exists(part):
                                    candidates.append(part)
            except Exception:
                pass
        
        # 4. Standard Windows Paths
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        user_home = os.path.expanduser('~')
        
        for ver in ['313', '312', '311', '310', '39', '38']:
            candidates.extend([
                os.path.join(local_app_data, 'Programs', 'Python', f'Python{ver}', 'python.exe'),
                os.path.join(program_files, f'Python{ver}', 'python.exe'),
                f'C:\\Python{ver}\\python.exe',
                os.path.join(user_home, 'AppData', 'Local', 'Programs', 'Python', f'Python{ver}', 'python.exe'),
            ])
        
        # 5. System PATH
        path_dirs = os.environ.get('PATH', '').split(os.pathsep)
        for p in path_dirs:
            p_exe = os.path.join(p, 'python.exe')
            if os.path.exists(p_exe):
                candidates.append(p_exe)
        
        # Validate and return first working candidate
        seen = set()
        for path in candidates:
            if not path: continue
            try: path = os.path.normpath(os.path.abspath(path))
            except: continue
            
            if path in seen: continue
            seen.add(path)
            
            if validate_python_executable(path):
                self.logger.info(f"Found valid Python: {path}")
                self._cached_python_path = path
                return path
        
        # Fallback
        self.logger.warning("No valid Python found.")
        self._cached_python_path = sys.executable
        return self._cached_python_path

    def check_health(self) -> Dict[str, Any]:
        """Perform comprehensive system health check."""
        results = { "status": "HEALTHY", "timestamp": time.time(), "details": {} }
        
        # Check Critical Resources
        resources = ["pyscope/ui.py", "pyscope/core.py"]
        missing = [r for r in resources if not os.path.exists(self.get_resource_path(r))]
        if missing:
            results["status"] = "DEGRADED"
            results["details"]["missing_resources"] = missing
        
        # Check Network
        try:
            socket.create_connection(("pypi.org", 443), timeout=3)
            results["details"]["network"] = "CONNECTED"
        except OSError:
            results["details"]["network"] = "OFFLINE"
            
        results["details"]["platform"] = platform.system()
        results["details"]["is_frozen"] = self.is_frozen
        
        # Validate Python & Pip
        python_path = self.get_actual_python_executable()
        results["details"]["python_path"] = python_path
        
        try:
            # Check Python
            proc = subprocess.run(
                [python_path, "-c", "import sys; print(sys.version_info[:2])"],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                results["details"]["python_valid"] = True
            else:
                results["details"]["python_valid"] = False
                results["status"] = "CRITICAL"
                results["details"]["python_error"] = proc.stderr
        except Exception as e:
            results["details"]["python_valid"] = False
            results["status"] = "CRITICAL"
            results["details"]["python_error"] = str(e)
        
        try:
            # Check Pip
            proc = subprocess.run(
                [python_path, "-m", "pip", "--version"],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0:
                results["details"]["pip_available"] = True
                results["details"]["pip_version"] = proc.stdout.strip()[:50]
            else:
                results["details"]["pip_available"] = False
                results["status"] = "CRITICAL" if self.is_frozen else "DEGRADED"
        except Exception as e:
            results["details"]["pip_available"] = False
            results["status"] = "CRITICAL" if self.is_frozen else "DEGRADED"
            results["details"]["pip_error"] = str(e)
        
        return results


# Singleton
_instance = None

def get_detector(app_name: str = "PyScope") -> SystemDetector:
    global _instance
    if _instance is None:
        _instance = SystemDetector(app_name)
    return _instance
