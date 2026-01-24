"""📦 PyPI Manager - Environment Management Module"""

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
from utils import logger, safe_string_truncate


def discover_python_installations() -> List[Dict]:
    """Discover all Python installations on the system."""
    pythons = []
    seen_paths = set()
    
    # Get system Python first
    system_python = sys.executable
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
        result = subprocess.run(
            ["py", "--list-paths"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=True
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


def discover_virtual_environments() -> List[Dict]:
    """Discover all virtual environments on the system."""
    venvs = []
    seen_paths = set()
    
    home = Path.home()
    search_locations = []
    
    search_locations.append((home, 2))
    
    env_dirs = [
        home / ".virtualenvs",
        home / ".venvs",
        home / "venvs",
        home / "envs",
        home / ".pyenv" / "versions",
    ]
    
    conda_locations = [
        home / "anaconda3" / "envs",
        home / "miniconda3" / "envs",
        home / ".conda" / "envs",
    ]
    
    for env_dir in env_dirs + conda_locations:
        if env_dir.exists():
            search_locations.append((env_dir, 1))
    
    # Search each location
    for base_path, max_depth in search_locations:
        try:
            _search_for_venvs(base_path, venvs, seen_paths, max_depth, 0)
        except (PermissionError, OSError) as e:
            logger.debug(f"Permission denied for {base_path}: {e}")
        except Exception as e:
            logger.debug(f"Error searching {base_path}: {e}")
    
    # Sort by name
    venvs.sort(key=lambda x: x.get("name", "").lower())
    
    return venvs


def _search_for_venvs(base_path: Path, venvs: List[Dict], seen_paths: set, max_depth: int, current_depth: int):
    """Recursively search for virtual environments."""
    if current_depth > max_depth:
        return
    
    try:
        for item in base_path.iterdir():
            if item.name.startswith('.') or item.name in ('__pycache__', 'node_modules', '.git'):
                continue
            
            if item.is_dir():
                venv_info = check_if_venv(item)
                if venv_info:
                    real_path = os.path.realpath(str(item))
                    if real_path not in seen_paths:
                        venvs.append(venv_info)
                        seen_paths.add(real_path)
                else:
                    _search_for_venvs(item, venvs, seen_paths, max_depth, current_depth + 1)
    except (PermissionError, OSError):
        pass


def check_if_venv(folder_path: Path) -> Optional[Dict]:
    """Check if a folder is a virtual environment."""
    try:
        if isinstance(folder_path, str):
            folder_path = Path(folder_path)
        
        if not folder_path.exists() or not folder_path.is_dir():
            return None
        
        # Check for conda environment
        conda_meta = folder_path / "conda-meta"
        if conda_meta.exists() and conda_meta.is_dir():
            return _get_conda_env_info(folder_path)
        
        # Check for venv/virtualenv
        if platform.system() == "Windows":
            python_exe = folder_path / "Scripts" / "python.exe"
            pip_exe = folder_path / "Scripts" / "pip.exe"
        else:
            python_exe = folder_path / "bin" / "python"
            pip_exe = folder_path / "bin" / "pip"
        
        pyvenv_cfg = folder_path / "pyvenv.cfg"
        activate_script = folder_path / ("Scripts/activate.bat" if platform.system() == "Windows" else "bin/activate")
        
        if python_exe.exists():
            python_info = get_python_info(str(python_exe))
            if not python_info:
                return None
            
            env_type = "venv" if pyvenv_cfg.exists() else "virtualenv"
            pip_path = str(pip_exe) if pip_exe.exists() else None
            env_name = folder_path.name
            
            return {
                "name": env_name,
                "path": str(folder_path),
                "python_path": str(python_exe),
                "pip_path": pip_path,
                "type": env_type,
                "version": python_info.get("version", "Unknown"),
                "display": f"📦 {env_name} ({env_type}) - {python_info.get('version', 'Unknown')}"
            }
        
        return None
        
    except Exception as e:
        logger.debug(f"Error checking venv {folder_path}: {e}")
        return None


def _get_conda_env_info(env_path: Path) -> Optional[Dict]:
    """Get conda environment information."""
    try:
        if platform.system() == "Windows":
            python_exe = env_path / "python.exe"
            if not python_exe.exists():
                python_exe = env_path / "Scripts" / "python.exe"
            pip_exe = env_path / "Scripts" / "pip.exe"
        else:
            python_exe = env_path / "bin" / "python"
            pip_exe = env_path / "bin" / "pip"
        
        if not python_exe.exists():
            return None
        
        python_info = get_python_info(str(python_exe))
        if not python_info:
            return None
        
        env_name = env_path.name
        pip_path = str(pip_exe) if pip_exe.exists() else None
        
        return {
            "name": env_name,
            "path": str(env_path),
            "python_path": str(python_exe),
            "pip_path": pip_path,
            "type": "conda",
            "version": python_info.get("version", "Unknown"),
            "display": f"🐍 {env_name} (conda) - {python_info.get('version', 'Unknown')}"
        }
        
    except Exception as e:
        logger.debug(f"Error getting conda env info: {e}")
        return None


def get_python_info(python_path: str) -> Optional[Dict]:
    """Get Python version information."""
    try:
        if not os.path.exists(python_path):
            return None
        
        # Run python --version
        result = subprocess.run(
            [python_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False
        )
        
        if result.returncode == 0:
            version_output = result.stdout.strip()
            version_match = re.search(r"Python\s+(\d+\.\d+\.\d+)", version_output)
            if version_match:
                version = f"Python {version_match.group(1)}"
            else:
                version = version_output
            
            display = f"🐍 {version} ({python_path})"
            
            return {
                "path": python_path,
                "version": version,
                "display": display
            }
        else:
            # Alternative method
            try:
                result = subprocess.run(
                    [python_path, "-c", "import sys; print(f'Python {sys.version}')"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
                    return {
                        "path": python_path,
                        "version": version,
                        "display": f"🐍 {version} ({python_path})"
                    }
            except Exception:
                pass
            
            return None
            
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as e:
        logger.debug(f"Failed to get Python info for {python_path}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Unexpected error getting Python info: {e}")
        return None


def _parse_version(version_str: str) -> Tuple[int, int, int]:
    """Parse version string to tuple for sorting."""
    try:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_str)
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        
        match = re.search(r"(\d+)\.(\d+)", version_str)
        if match:
            return (int(match.group(1)), int(match.group(2)), 0)
        
        match = re.search(r"(\d+)", version_str)
        if match:
            return (int(match.group(1)), 0, 0)
        
        return (0, 0, 0)
    except Exception:
        return (0, 0, 0)


class EnvironmentManager:
    """Manages Python environments and installations with package state caching."""
    
    def __init__(self):
        self.lock = threading.RLock()
        self.current_env = None
        self.pythons = []
        self.venvs = []
        self.all_environments = []
        
        # Package state cache with size limits
        self._package_states_cache = OrderedDict()
        self._cache_expiry = timedelta(hours=2)
        self._cache_max_size = 50
        
        # Default to system Python
        self._init_default_environment()
        
        # Initial refresh
        self.refresh()
        
        logger.info("EnvironmentManager initialized with package state caching")
    
    def _trim_cache(self, cache: OrderedDict, max_size: int):
        """Trim cache to maintain maximum size limit."""
        while len(cache) > max_size:
            cache.popitem(last=False)
    
    def _init_default_environment(self):
        """Initialize default system environment."""
        system_python = sys.executable
        if system_python:
            info = get_python_info(system_python)
            if info:
                self.current_env = {
                    "type": "system",
                    "python_path": system_python,
                    "pip_path": None,
                    "display": "💻 System Python",
                    "version": info.get("version", "Unknown"),
                    "env_id": self._generate_env_id(system_python, None)
                }
            else:
                self.current_env = {
                    "type": "system",
                    "python_path": system_python,
                    "pip_path": None,
                    "display": "💻 System Python",
                    "version": "Unknown",
                    "env_id": self._generate_env_id(system_python, None)
                }
    
    def _generate_env_id(self, python_path: str, env_path: Optional[str]) -> str:
        """Generate unique ID for environment."""
        if env_path:
            return hashlib.md5(env_path.encode()).hexdigest()[:16]
        elif python_path:
            return hashlib.md5(python_path.encode()).hexdigest()[:16]
        else:
            return hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[:16]
    
    def refresh(self):
        """Refresh all environment lists."""
        with self.lock:
            self.pythons = []
            self.venvs = []
            self.all_environments = []
            
            def discover_pythons():
                try:
                    self.pythons = discover_python_installations()
                except Exception as e:
                    logger.error(f"Failed to discover Python installations: {e}")
                    self.pythons = []
            
            def discover_venvs():
                try:
                    self.venvs = discover_virtual_environments()
                except Exception as e:
                    logger.error(f"Failed to discover virtual environments: {e}")
                    self.venvs = []
            
            # Run discovery in threads
            threads = []
            for target in [discover_pythons, discover_venvs]:
                thread = threading.Thread(target=target, daemon=True)
                thread.start()
                threads.append(thread)
            
            # Wait for completion with timeout
            for thread in threads:
                thread.join(timeout=10)
            
            self._build_combined_list()
            self._clean_expired_cache()
    
    def _build_combined_list(self):
        """Build combined list of all environments."""
        with self.lock:
            self.all_environments = []
            
            # Add system Python first
            if self.current_env:
                self.current_env["env_id"] = self._generate_env_id(
                    self.current_env.get("python_path", ""),
                    self.current_env.get("path", "")
                )
                self.all_environments.append(self.current_env.copy())
            
            # Add other Python installations
            for python_info in self.pythons:
                python_path = python_info.get("path")
                if python_path and python_path != (self.current_env.get("python_path") if self.current_env else None):
                    env_id = self._generate_env_id(python_path, None)
                    self.all_environments.append({
                        "type": "python",
                        "python_path": python_path,
                        "pip_path": None,
                        "display": python_info.get("display", f"Python ({python_path})"),
                        "version": python_info.get("version", "Unknown"),
                        "env_id": env_id
                    })
            
            # Add virtual environments
            for venv_info in self.venvs:
                env_id = self._generate_env_id(
                    venv_info.get("python_path", ""),
                    venv_info.get("path", "")
                )
                self.all_environments.append({
                    "type": venv_info.get("type", "venv"),
                    "name": venv_info.get("name", ""),
                    "path": venv_info.get("path", ""),
                    "python_path": venv_info.get("python_path", ""),
                    "pip_path": venv_info.get("pip_path"),
                    "display": venv_info.get("display", ""),
                    "version": venv_info.get("version", "Unknown"),
                    "env_id": env_id
                })
    
    def get_all_environments(self) -> List[Dict]:
        """Get all available environments."""
        with self.lock:
            return self.all_environments.copy()
    
    def set_environment(self, env_info: Dict):
        """Set the current environment."""
        with self.lock:
            if "env_id" not in env_info:
                env_info["env_id"] = self._generate_env_id(
                    env_info.get("python_path", ""),
                    env_info.get("path", "")
                )
            
            self.current_env = env_info.copy()
            
            # Validate paths
            python_path = env_info.get("python_path")
            if python_path and not os.path.exists(python_path):
                logger.warning(f"Python path does not exist: {python_path}")
            
            pip_path = env_info.get("pip_path")
            if pip_path and not os.path.exists(pip_path):
                logger.warning(f"Pip path does not exist: {pip_path}")
            
            logger.info(f"Switched to environment: {env_info.get('display', 'Unknown')}")
    
    def get_pip_command(self) -> List[str]:
        """Get pip command for current environment."""
        with self.lock:
            if not self.current_env:
                return [sys.executable, "-m", "pip"]
            
            pip_path = self.current_env.get("pip_path")
            python_path = self.current_env.get("python_path")
            
            if pip_path and os.path.exists(pip_path):
                return [pip_path]
            elif python_path and os.path.exists(python_path):
                return [python_path, "-m", "pip"]
            else:
                return [sys.executable, "-m", "pip"]
    
    def get_python_command(self) -> str:
        """Get Python command for current environment."""
        with self.lock:
            if not self.current_env:
                return sys.executable
            
            python_path = self.current_env.get("python_path")
            if python_path and os.path.exists(python_path):
                return python_path
            else:
                return sys.executable
    
    def get_current_display(self) -> str:
        """Get display string for current environment."""
        with self.lock:
            if not self.current_env:
                return "💻 System Python"
            
            return self.current_env.get("display", "💻 System Python")
    
    def get_current_env_id(self) -> Optional[str]:
        """Get current environment ID."""
        with self.lock:
            if not self.current_env:
                return None
            
            return self.current_env.get("env_id")
    
    def find_environment_by_python(self, python_path: str) -> Optional[Dict]:
        """Find environment by Python executable path."""
        with self.lock:
            for env in self.all_environments:
                if env.get("python_path") == python_path:
                    return env
            return None
    
    def save_packages_state(self, packages: List[Dict]):
        """Save packages state for current environment."""
        if not self.current_env or not packages:
            return
        
        env_id = self.current_env.get("env_id")
        if not env_id:
            return
        
        with self.lock:
            package_states = {}
            for pkg in packages:
                name = pkg.get("name", "")
                if name:
                    package_states[name] = {
                        "lat": pkg.get("lat", "Unknown"),
                        "stat": pkg.get("stat", "Unknown"),
                        "timestamp": datetime.now()
                    }
            
            self._package_states_cache[env_id] = {
                "states": package_states,
                "timestamp": datetime.now()
            }
            self._trim_cache(self._package_states_cache, self._cache_max_size)
            
            logger.info(f"Saved {len(package_states)} packages state for environment: {env_id}")
    
    def restore_packages_state(self, raw_packages: List[Dict]) -> List[Dict]:
        """Restore packages state for current environment."""
        if not self.current_env or not raw_packages:
            return raw_packages
        
        env_id = self.current_env.get("env_id")
        if not env_id:
            return raw_packages
        
        with self.lock:
            cache_entry = self._package_states_cache.get(env_id)
            if not cache_entry:
                return raw_packages
            
            cache_time = cache_entry.get("timestamp", datetime.min)
            if datetime.now() - cache_time > self._cache_expiry:
                logger.debug(f"Cache expired for environment: {env_id}")
                del self._package_states_cache[env_id]
                return raw_packages
            
            states = cache_entry.get("states", {})
            restored_packages = []
            cache_hits = 0
            
            for pkg in raw_packages:
                name = pkg.get("name", "")
                if name in states:
                    cache_hits += 1
                    cached = states[name]
                    restored_packages.append({
                        "name": name,
                        "ver": pkg.get("ver", ""),
                        "lat": cached.get("lat", "Unknown"),
                        "stat": cached.get("stat", "Unknown")
                    })
                else:
                    restored_packages.append({
                        "name": name,
                        "ver": pkg.get("ver", ""),
                        "lat": "Unknown",
                        "stat": "Unknown"
                    })
            
            logger.info(f"Restored {cache_hits}/{len(raw_packages)} packages from cache for environment: {env_id}")
            return restored_packages
    
    def clear_environment_cache(self, env_id: Optional[str] = None):
        """Clear cache for specific environment or all."""
        with self.lock:
            if env_id:
                if env_id in self._package_states_cache:
                    del self._package_states_cache[env_id]
                    logger.info(f"Cleared cache for environment: {env_id}")
            else:
                self._package_states_cache.clear()
                logger.info("Cleared all environment caches")
    
    def _clean_expired_cache(self):
        """Clean expired cache entries."""
        with self.lock:
            to_delete = []
            current_time = datetime.now()
            
            for env_id, cache_entry in self._package_states_cache.items():
                cache_time = cache_entry.get("timestamp", datetime.min)
                if current_time - cache_time > self._cache_expiry:
                    to_delete.append(env_id)
            
            for env_id in to_delete:
                del self._package_states_cache[env_id]
            
            if to_delete:
                logger.debug(f"Cleaned {len(to_delete)} expired cache entries")
    
    def get_cached_environments(self) -> List[str]:
        """Get list of environments with cached package states."""
        with self.lock:
            return list(self._package_states_cache.keys())
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        with self.lock:
            return {
                "current_env": self.current_env,
                "environments_count": len(self.all_environments),
                "cached_environments": len(self._package_states_cache)
            }