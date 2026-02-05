"""PyScope:https://github.com/Limitless-Soul1/PyScope"""

import os
import sys
import subprocess
import threading
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import re
import platform
import hashlib
from datetime import datetime, timedelta
from collections import OrderedDict
from .utils import logger, safe_string_truncate


def discover_python_installations() -> List[Dict]:
    """Discover all Python installations on the system."""
    pythons = []
    seen_paths = set()
    
    # Get actual Python interpreter
    from .system import get_detector
    detector = get_detector()
    system_python = detector.get_actual_python_executable()
    
    if system_python:
        try:
            info = get_python_info(system_python)
            if info:
                pythons.append(info)
                seen_paths.add(os.path.realpath(system_python))
        except Exception as e:
            logger.warning(f"Failed to get system Python info: {e}")
    
    # Platform-specific discovery
    if platform.system() == "Windows":
        pythons.extend(_discover_windows_pythons(seen_paths))
    else:  # Linux/macOS
        pythons.extend(_discover_unix_pythons(seen_paths))
    
    # Sort by version (newest first)
    pythons.sort(key=lambda x: _parse_version(x.get("version", "")), reverse=True)
    
    return pythons


def _discover_windows_pythons(seen_paths: set) -> List[Dict]:
    """Discover Python installations on Windows."""
    pythons = []
    
    # Use py launcher if available
    try:
        # Hide console window
        startupinfo = None
        creationflags = 0
        if platform.system() == 'Windows':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        
        result = subprocess.run(
            ["py", "--list-paths"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            startupinfo=startupinfo,
            creationflags=creationflags
        )
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.strip() and '|' in line:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        path = parts[1].strip()
                        if os.path.exists(path):
                            real_path = os.path.realpath(path)
                            if real_path not in seen_paths:
                                info = get_python_info(path)
                                if info:
                                    pythons.append(info)
                                    seen_paths.add(real_path)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug(f"py launcher not available: {e}")
    
    # Common installation paths
    search_paths = []
    current_drive = os.path.splitdrive(os.getcwd())[0]
    
    search_paths.append(f"{current_drive}\\Python*")
    search_paths.append(os.path.expandvars("%ProgramFiles%\\Python*"))
    search_paths.append(os.path.expandvars("%ProgramFiles(x86)%\\Python*"))
    search_paths.append(os.path.expandvars("%LOCALAPPDATA%\\Programs\\Python\\Python*"))
    search_paths.append(os.path.expandvars("%APPDATA%\\Python\\Python*"))
    
    for pattern in search_paths:
        try:
            for python_dir in Path("/").glob(pattern) if pattern.startswith("/") else Path(pattern[0] + ":\\").glob(pattern[3:]):
                exe_path = python_dir / "python.exe"
                if exe_path.exists():
                    real_path = os.path.realpath(str(exe_path))
                    if real_path not in seen_paths:
                        info = get_python_info(str(exe_path))
                        if info:
                            pythons.append(info)
                            seen_paths.add(real_path)
        except Exception as e:
            logger.debug(f"Failed to search {pattern}: {e}")
    
    return pythons


def _discover_unix_pythons(seen_paths: set) -> List[Dict]:
    """Discover Python installations on Unix-like systems."""
    pythons = []
    
    # Use which command
    for binary in ["python", "python3"]:
        try:
            result = subprocess.run(
                ["which", "-a", binary],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False
            )
            
            if result.returncode == 0:
                for path in result.stdout.strip().split('\n'):
                    if path.strip() and os.path.exists(path):
                        real_path = os.path.realpath(path)
                        if real_path not in seen_paths:
                            info = get_python_info(path)
                            if info:
                                pythons.append(info)
                                seen_paths.add(real_path)
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"which failed for {binary}: {e}")
    
    # Common paths
    common_paths = [
        "/usr/bin/python*",
        "/usr/local/bin/python*",
        "/opt/homebrew/bin/python*",
        f"{Path.home()}/.pyenv/versions/*/bin/python*",
        f"{Path.home()}/.local/bin/python*",
    ]
    
    for pattern in common_paths:
        try:
            for path in Path("/").glob(pattern.lstrip("/")) if pattern.startswith("/") else Path.home().glob(pattern):
                if path.exists() and os.access(str(path), os.X_OK):
                    real_path = os.path.realpath(str(path))
                    if real_path not in seen_paths:
                        if os.path.islink(str(path)):
                            target = os.path.realpath(str(path))
                            if target in seen_paths:
                                continue
                        
                        info = get_python_info(str(path))
                        if info:
                            pythons.append(info)
                            seen_paths.add(real_path)
        except Exception as e:
            logger.debug(f"Failed to search {pattern}: {e}")
    
    return pythons


def get_python_info(python_path: str) -> Optional[Dict]:
    """Get information about a Python installation."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            version = result.stdout.strip()
            return {
                "python_path": python_path,
                "version": version,
                "display": f"Python {version} ({os.path.dirname(python_path)})",
                "type": "system"
            }
    except Exception as e:
        logger.debug(f"Failed to get info for {python_path}: {e}")
    
    return None


def _parse_version(version_str: str) -> Tuple:
    """Parse version string for comparison."""
    try:
        parts = version_str.split('.')
        return tuple(int(p) for p in parts[:3])
    except:
        return (0, 0, 0)


def safe_string_truncate(s: str, max_len: int = 100) -> str:
    """Safely truncate a string."""
    if not s:
        return ""
    return s[:max_len] + "..." if len(s) > max_len else s


class EnvironmentManager:
    """Manages Python environments for the application."""
    
    def __init__(self):
        self.lock = threading.RLock()
        self.all_environments = []
        self.current_env = None
        self._init_default_environment()
        self.refresh()
    
    def _init_default_environment(self):
        """Initialize with the default Python environment."""
        from .system import get_detector
        detector = get_detector()
        
        # Use detector to get the actual python (handles frozen mode)
        python_path = detector.get_actual_python_executable()
        
        try:
            result = subprocess.run(
                [python_path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                capture_output=True,
                text=True,
                timeout=5
            )
            version = result.stdout.strip() if result.returncode == 0 else "Unknown"
        except:
            version = "Unknown"
        
        self.current_env = {
            "type": "system",
            "python_path": python_path,
            "pip_path": None,
            "display": f"System Python {version}",
            "version": version
        }
        self.all_environments = [self.current_env]
        logger.info(f"Default environment set: {python_path}")
    
    def refresh(self):
        """Refresh the list of available environments."""
        with self.lock:
            try:
                pythons = discover_python_installations()
                venvs = self._discover_virtual_environments()
                
                self.all_environments = []
                
                # Add discovered Pythons
                for py in pythons:
                    env = {
                        "type": "python",
                        "python_path": py["python_path"],
                        "pip_path": None,
                        "display": py["display"],
                        "version": py["version"]
                    }
                    self.all_environments.append(env)
                
                # Add virtual environments
                for venv in venvs:
                    self.all_environments.append(venv)
                
                # Ensure current environment is in the list
                if self.current_env and self.current_env not in self.all_environments:
                    self.all_environments.insert(0, self.current_env)
                
                logger.info(f"Refreshed environments: {len(self.all_environments)} found")
                
            except Exception as e:
                logger.error(f"Failed to refresh environments: {e}")
    
    def _discover_virtual_environments(self) -> List[Dict]:
        """Discover virtual environments with deep recursive search."""
        venvs = []
        seen_paths = set()
        
        # 1. Recursive search in common locations
        search_locations = [
            Path(os.getcwd()),
            Path.home(),
            Path.home() / "Projects",
            Path.home() / "Code",
            Path.home() / "Documents",
            Path.home() / "Desktop",
        ]
        
        # Add parent directories of cwd
        cwd = Path.cwd()
        for _ in range(3):
            parent = cwd.parent
            if parent != cwd and parent not in search_locations:
                search_locations.append(parent)
                cwd = parent
            else:
                break
        
        # Recursive search in each location
        for location in search_locations:
            if location.exists() and location.is_dir():
                self._search_for_venvs(location, venvs, seen_paths, max_depth=3)
        
        # 2. Conda environment discovery
        conda_envs = self._discover_conda_environments(seen_paths)
        venvs.extend(conda_envs)
        
        # 3. Pyenv discovery
        try:
            # PyenvDetector class is defined in this file now
            pyenv_detector = PyenvDetector()
            pyenv_envs = pyenv_detector.detect(seen_paths)
            venvs.extend(pyenv_envs)
        except ImportError:
            logger.debug("PyenvDetector not available")
        except Exception as e:
             logger.error(f"Pyenv discovery failed: {e}")
        
        return venvs

    def is_protected_windows_folder(self, folder_name: str) -> bool:
        """Check if folder is a protected Windows system folder"""
        protected_folders = {
            "Application Data", "Local Settings", "Temporary Internet Files",
            "Cookies", "History", "NetHood", "PrintHood", "Recent",
            "SendTo", "Start Menu", "Templates", "My Documents",
            "My Music", "My Pictures", "My Videos", "Desktop",
            "Favorites", "Links", "Saved Games", "Searches",
            "System Volume Information", "$RECYCLE.BIN", "Windows",
            "Program Files", "Program Files (x86)", "ProgramData"
        }
        return folder_name in protected_folders

    def _search_for_venvs(self, base_path: Path, venvs: List[Dict], seen_paths: set, max_depth: int, current_depth: int = 0):
        """Recursively search for virtual environments."""
        if current_depth > max_depth or not base_path.exists() or not base_path.is_dir():
            return
        
        # Guard against protected Windows folders early
        if platform.system() == "Windows" and self.is_protected_windows_folder(base_path.name):
            return
        
        try:
            # Check if this folder is a venv
            venv_info = self._check_venv_in_path(base_path, seen_paths)
            if venv_info:
                venvs.append(venv_info)
                # Don't recurse into a venv itself usually, but we mark python path as seen
                seen_paths.add(Path(venv_info["python_path"]).resolve())
            
            # Ignore specific directories to optimize search
            ignore_dirs = {
                ".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".env", 
                "Lib", "Include", "Scripts", "build", "dist", ".idea", ".vscode",
                "Application Data", "Local Settings", "Temporary Internet Files",
                "Cookies", "History", "NetHood", "PrintHood", "Recent", 
                "SendTo", "Start Menu", "Templates", "My Documents", "My Music",
                "My Pictures", "My Videos"
            }
            
            # Recurse into subdirectories
            for item in base_path.iterdir():
                # SECURITY: Validate symlinks to prevent path traversal loops and escaping to system dirs
                if item.is_symlink():
                    try:
                        target = item.resolve()
                        # Reject if target escapes base search path (prevent traversing into /etc, /root, etc.)
                        if not str(target).startswith(str(base_path.resolve())):
                            continue
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Failed to resolve symlink {item}: {e}")
                        continue
                    
                if item.is_dir() and item.name not in ignore_dirs and not item.name.startswith('.'):
                    # Skip very long paths
                    if len(str(item)) > 300:
                        continue
                    self._search_for_venvs(item, venvs, seen_paths, max_depth, current_depth + 1)
                    
        except (PermissionError, OSError) as e:
            logger.debug(f"Permission denied accessing {base_path}: {e}")
        except Exception as e:
            logger.debug(f"Error scanning {base_path}: {e}")

    def _check_venv_in_path(self, path: Path, seen_paths: set) -> Optional[Dict]:
        """Check if path contains a virtual environment."""
        # Check explicit venv folders inside this path OR if the path itself is a venv
        candidates = [path]  # Check if 'path' IS the venv
        
        # Also check standard subfolder names if we are not already inside one
        venv_names = ["venv", ".venv", "env", ".env"]
        for name in venv_names:
            candidates.append(path / name)
            
        for venv_path in candidates:
            if venv_path.exists() and venv_path.is_dir():
                # Check for python executable
                if platform.system() == "Windows":
                    python_exe = venv_path / "Scripts" / "python.exe"
                else:
                    python_exe = venv_path / "bin" / "python"
                
                if python_exe.exists():
                    try:
                        real_path = python_exe.resolve()
                        if real_path in seen_paths:
                            continue
                        
                        # Verify it looks like a venv (pyvenv.cfg or site-packages)
                        is_valid = False
                        if (venv_path / "pyvenv.cfg").exists():
                            is_valid = True
                        elif (venv_path / "Lib" / "site-packages").exists(): # Windows
                            is_valid = True
                        elif (venv_path / "lib").exists(): # Unix check
                            # Lazy check for site-packages in lib/pythonX.Y/
                            for sub in (venv_path / "lib").glob("python*"):
                                if (sub / "site-packages").exists():
                                    is_valid = True
                                    break
                                    
                        if is_valid:
                            python_info = get_python_info(str(python_exe))
                            if python_info:
                                seen_paths.add(real_path)
                                name_display = venv_path.name
                                if venv_path == path:
                                     # If the path itself is the venv
                                     name_display = path.name
                                else:
                                     # If it's a subfolder like /project/venv
                                     name_display = f"{path.name}/{venv_path.name}"

                                return {
                                    "type": "venv",
                                    "python_path": str(python_exe),
                                    "pip_path": None,
                                    "path": str(venv_path),
                                    "display": f"venv: {name_display}",
                                    "version": python_info.get("version", "Unknown")
                                }
                    except Exception:
                        pass
        return None

    def _discover_conda_environments(self, seen_paths: set) -> List[Dict]:
        """Discover ALL Conda environments (not just active one)."""
        conda_envs = []
        
        # 1. Active Conda environment
        if os.environ.get('CONDA_PREFIX'):
            try:
                conda_path = Path(os.environ['CONDA_PREFIX'])
                python_path = conda_path / ("python.exe" if platform.system() == "Windows" else "bin/python")
                if python_path.exists():
                    real_path = python_path.resolve()
                    if real_path not in seen_paths:
                        info = get_python_info(str(python_path))
                        if info:
                            conda_envs.append({
                                "type": "conda",
                                "python_path": str(python_path),
                                "pip_path": None,
                                "path": str(conda_path),
                                "display": f"conda: (active)",
                                "version": info.get("version", "Unknown")
                            })
                            seen_paths.add(real_path)
            except Exception:
                pass
        
        # 2. Standard Conda locations
        conda_locations = []
        if platform.system() == "Windows":
            conda_locations.extend([
                Path.home() / "Anaconda3" / "envs",
                Path.home() / "Miniconda3" / "envs",
                Path(os.environ.get('LOCALAPPDATA', '')) / 'anaconda3' / 'envs',
                Path(os.environ.get('LOCALAPPDATA', '')) / 'miniconda3' / 'envs',
            ])
        else:  # Linux/macOS
            conda_locations.extend([
                Path.home() / "anaconda3" / "envs",
                Path.home() / "miniconda3" / "envs",
                Path.home() / ".conda" / "envs",
            ])
        
        # Scan environments
        for envs_dir in conda_locations:
            if not envs_dir.exists() or not envs_dir.is_dir():
                continue
            
            try:
                for env_name in envs_dir.iterdir():
                    if not env_name.is_dir():
                        continue
                    
                    # Verify it's a conda env (has conda-meta)
                    if not (env_name / "conda-meta").exists():
                        continue
                    
                    python_exe = env_name / ("python.exe" if platform.system() == "Windows" else "bin/python")
                    if python_exe.exists():
                        try:
                            real_path = python_exe.resolve()
                            if real_path in seen_paths:
                                continue
                            
                            info = get_python_info(str(python_exe))
                            if info:
                                conda_envs.append({
                                    "type": "conda",
                                    "python_path": str(python_exe),
                                    "pip_path": None,
                                    "path": str(env_name),
                                    "display": f"conda: {env_name.name}",
                                    "version": info.get("version", "Unknown")
                                })
                                seen_paths.add(real_path)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Error scanning conda envs at {envs_dir}: {e}")
        
        return conda_envs
    
    def get_all_environments(self) -> List[Dict]:
        """Get all discovered environments."""
        with self.lock:
            return list(self.all_environments)
    
    def set_environment(self, env: Dict):
        """Set the current environment."""
        with self.lock:
            self.current_env = env
            logger.info(f"Environment changed to: {env.get('display', 'Unknown')}")
    
    def get_pip_command(self) -> List[str]:
        """Get the pip command for the current environment."""
        # Import here to avoid circular imports
        from .system import get_detector
        
        if not self.current_env:
            # Use SystemDetector to get actual Python (handles frozen mode)
            detector = get_detector()
            python_path = detector.get_actual_python_executable()
            return [python_path, "-m", "pip"]
        
        python_path = self.current_env.get("python_path")
        if python_path and os.path.exists(python_path):
            return [python_path, "-m", "pip"]
        
        # Fallback: use SystemDetector (handles frozen mode)
        from .system import get_detector
        detector = get_detector()
        return [detector.get_actual_python_executable(), "-m", "pip"]
    
    def get_python_command(self) -> str:
        """Get the Python command for the current environment."""
        if self.current_env and self.current_env.get("python_path"):
            return self.current_env["python_path"]
        
        # Fallback: use SystemDetector (handles frozen mode)
        from .system import get_detector
        detector = get_detector()
        return detector.get_actual_python_executable()
    
    def get_current_display(self) -> str:
        """Get display name of current environment."""
        if self.current_env:
            return self.current_env.get("display", "Unknown")
        return "System Python"


# --- From pyenv_detector.py ---

class PyenvDetector:
    """Detects pyenv environments."""
    
    def detect(self, seen_paths: set = None) -> List[Dict]:
        """Detect all pyenv environments."""
        seen_paths = seen_paths or set()
        pyenv_envs = []
        
        # 1. Determine pyenv root
        pyenv_roots = [
            Path.home() / ".pyenv" / "versions",
            Path(os.environ.get("PYENV_ROOT", "")) / "versions",
        ]
        
        for pyenv_root in pyenv_roots:
            if not pyenv_root.exists() or not pyenv_root.is_dir():
                continue
                
            pyenv_envs.extend(self._scan_pyenv_root(pyenv_root, seen_paths))
        
        logger.info(f"Detected {len(pyenv_envs)} pyenv environments")
        return pyenv_envs
    
    def _scan_pyenv_root(self, root: Path, seen_paths: set) -> List[Dict]:
        """Scan pyenv root for environments."""
        environments = []
        
        try:
            for env_dir in root.iterdir():
                if not env_dir.is_dir():
                    continue
                
                # Look for python in bin/
                python_path = self._find_python_in_env(env_dir)
                if not python_path or not python_path.exists():
                    continue
                
                real_path = str(python_path.resolve())
                if real_path in seen_paths:
                    continue
                
                # Check if valid python
                info = self.get_python_info(str(python_path))
                if not info:
                    continue
                
                environments.append({
                    "type": "pyenv",
                    "python_path": str(python_path),
                    "pip_path": str(env_dir / "bin" / "pip") if (env_dir / "bin" / "pip").exists() else None,
                    "path": str(env_dir),
                    "display": f"pyenv: {env_dir.name}",
                    "version": info.get("version", "Unknown"),
                    "source": "pyenv"
                })
                seen_paths.add(real_path)
                
        except PermissionError as e:
            logger.warning(f"Permission denied accessing {root}: {e}")
        except Exception as e:
            logger.error(f"Error scanning {root}: {e}")
        
        return environments
    
    def _find_python_in_env(self, env_dir: Path) -> Optional[Path]:
        """Find python executable in environment."""
        # Potential Python paths
        candidates = [
            env_dir / "bin" / "python",
            env_dir / "bin" / "python3",
        ]
        
        # Add .exe on Windows (though pyenv is mainly Unix, pyenv-win exists)
        if os.name == "nt":
            candidates.extend([
                env_dir / "bin" / "python.exe",
                env_dir / "bin" / "python3.exe",
                env_dir / "python.exe", # sometimes in root for windows structured matches
            ])
        
        for candidate in candidates:
            if candidate.exists():
                return candidate
        
        return None

    def get_python_info(self, python_path: str) -> Optional[Dict]:
        """Get information about a Python installation."""
        try:
            result = subprocess.run(
                [python_path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                version = result.stdout.strip()
                return {
                    "python_path": python_path,
                    "version": version
                }
        except Exception as e:
            logger.debug(f"Failed to get info for {python_path}: {e}")
        
        return None