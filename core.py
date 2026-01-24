"""📦 PyPI Manager - Core Module"""

import threading
import urllib.request
import urllib.parse
import urllib.error
import json
import time
import re
import os
import ssl
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timedelta
from contextlib import closing
from typing import List, Dict, Optional, Tuple
from utils import logger, run_pip_with_real_progress
from collections import OrderedDict


class PackageManagerCore:
    """Core package management with thread safety, rate limiting, and caching."""
    
    def __init__(self):
        self.lock = threading.RLock()
        self._shutting_down = threading.Event()
        
        self.packages: list = []
        self.checking = False
        
        self.last_check_time = {}
        self.request_failures = {}
        
        # LRU cache implementation
        self._search_cache = OrderedDict()
        self._search_cache_ttl = timedelta(minutes=5)
        self._search_cache_max_size = 100
        
        self._packages_cache = OrderedDict()
        self._cache_expiry = timedelta(hours=1)
        self._packages_cache_max_size = 20
        
        self._thread_pool = ThreadPoolExecutor(max_workers=1)
        
        # Security limits
        self.MAX_SEARCH_LENGTH = 100
        self.MAX_JSON_SIZE = 30_000_000
        
        # Environment support
        self.pip_command = None
        
        # Check cancellation
        self._check_cancelled = threading.Event()
        
        # Network failure tracking
        self._consecutive_failures = 0
        self._failures_lock = threading.Lock()
        self._consecutive_failures_threshold = 10
        
        # Performance optimization
        self._check_delay = 0.3
        self._update_batch = []
        self._batch_lock = threading.RLock()
        self._last_batch_update = 0
        self._batch_update_interval = 0.5
        self._thread_semaphore = threading.Semaphore(2)
        
        logger.info("PackageManagerCore initialized")

    def _trim_cache(self, cache: OrderedDict, max_size: int):
        """Trim cache to maintain maximum size limit."""
        while len(cache) > max_size:
            cache.popitem(last=False)

    def check_updates(self, ui_start_callback, ui_progress_callback, 
                     ui_finish_callback, ui_package_callback=None):
        """Check for updates for all installed packages."""
        if self._shutting_down.is_set():
            if ui_finish_callback:
                ui_finish_callback()
            return
        
        with self.lock:
            if self.checking:
                if ui_finish_callback:
                    ui_finish_callback()
                return
            self.checking = True
            self._check_cancelled.clear()
        
        try:
            if ui_start_callback:
                ui_start_callback()
            
            threading.Thread(
                target=self._check_updates_safe_parallel,
                args=(ui_finish_callback, ui_package_callback),
                daemon=True
            ).start()
        except Exception as e:
            with self.lock:
                self.checking = False
            logger.error(f"Check error: {e}")

    def _check_updates_safe_parallel(self, ui_finish_callback, ui_package_callback=None):
        """Parallel update checking with failure protection."""
        try:
            with self.lock:
                packages_to_check = [(p["name"], p["ver"]) for p in self.packages]
                total = len(packages_to_check)
            
            if total == 0:
                if ui_finish_callback:
                    ui_finish_callback()
                with self.lock:
                    self.checking = False
                return
            
            # Reset failure counter
            with self._failures_lock:
                self._consecutive_failures = 0
            
            global_timeout = 180
            start_time = time.time()
            max_workers = min(2, total)
            
            logger.info(f"Starting parallel update check for {total} packages")
            
            # Reset batch system
            with self._batch_lock:
                self._update_batch = []
                self._last_batch_update = time.time()
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_package = {}
                for idx, (pkg_name, pkg_ver) in enumerate(packages_to_check):
                    if self._shutting_down.is_set() or self._check_cancelled.is_set():
                        logger.info("Update check cancelled during task submission")
                        break
                    
                    if idx % 3 == 0 and idx > 0:
                        time.sleep(self._check_delay * 3)
                    
                    future = executor.submit(
                        self._check_single_package_simple, 
                        pkg_name, pkg_ver, ui_package_callback
                    )
                    future_to_package[future] = (pkg_name, pkg_ver)
                
                # Process results as they complete
                try:
                    for future in as_completed(future_to_package.keys(), timeout=global_timeout):
                        if time.time() - start_time > global_timeout:
                            logger.warning("Global timeout reached for update check")
                            break
                        
                        if self._shutting_down.is_set() or self._check_cancelled.is_set():
                            logger.info("Update check cancelled during processing")
                            break
                        
                        try:
                            future.result(timeout=1)
                            with self._failures_lock:
                                self._consecutive_failures = 0
                        except Exception as e:
                            with self._failures_lock:
                                self._consecutive_failures += 1
                            
                            logger.warning(f"Package check failed: {e}")
                            
                            if self._consecutive_failures >= self._consecutive_failures_threshold:
                                logger.error(f"Stopping update check due to consecutive failures")
                                break
                    
                except TimeoutError:
                    logger.warning(f"Global timeout reached for update check")
                except Exception as e:
                    logger.error(f"Error in parallel update check: {e}")
            
            # Flush any remaining batch updates
            self._flush_batch_updates(ui_package_callback)
        
        except Exception as e:
            logger.error(f"Parallel update thread error: {e}")
        finally:
            with self.lock:
                self.checking = False
            if not self._shutting_down.is_set() and ui_finish_callback:
                ui_finish_callback()

    def _flush_batch_updates(self, ui_package_callback):
        """Flush pending batch updates to UI."""
        if not ui_package_callback:
            return
        
        with self._batch_lock:
            if self._update_batch:
                try:
                    for pkg_name in self._update_batch:
                        ui_package_callback(pkg_name)
                    self._update_batch.clear()
                except Exception as e:
                    logger.error(f"Error flushing batch updates: {e}")

    def check_single_package(self, pkg_name: str, callback=None):
        """Check a single package for updates."""
        if self._shutting_down.is_set():
            if callback:
                callback(False, "Shutting down")
            return
        
        def check_task():
            try:
                logger.info(f"Checking single package: {pkg_name}")
                
                with self.lock:
                    package_info = None
                    current_version = "Unknown"
                    for p in self.packages:
                        if p["name"] == pkg_name:
                            package_info = p.copy()
                            current_version = p.get("ver", "Unknown")
                            break
                
                if not package_info:
                    if callback:
                        callback(False, f"Package {pkg_name} not found in local packages")
                    return
                
                success = self._check_single_package_simple(pkg_name, current_version, None)
                
                with self.lock:
                    updated_info = None
                    for p in self.packages:
                        if p["name"] == pkg_name:
                            updated_info = p.copy()
                            break
                
                if updated_info:
                    logger.info(f"Single package check completed for {pkg_name}: {updated_info['stat']}")
                    if callback:
                        callback(True, None)
                else:
                    logger.error(f"Failed to get updated info for {pkg_name}")
                    if callback:
                        callback(False, "Failed to update package information")
                        
            except Exception as e:
                logger.error(f"Error checking single package {pkg_name}: {e}")
                if callback:
                    callback(False, str(e))
        
        # Run with semaphore control
        def task_with_semaphore():
            with self._thread_semaphore:
                check_task()
        
        threading.Thread(target=task_with_semaphore, daemon=True).start()

    def _fetch_package_info(self, pkg_name: str):
        """Fetch package information from PyPI with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = f"https://pypi.org/pypi/{pkg_name}/json"
                req = urllib.request.Request(url, headers={'User-Agent': 'PyScope'})
                
                context = ssl.create_default_context()
                with closing(urllib.request.urlopen(req, timeout=10, context=context)) as response:
                    raw = response.read(self.MAX_JSON_SIZE + 1)
                    
                    if len(raw) > self.MAX_JSON_SIZE:
                        raise ValueError("Response too large")
                    
                    data = json.loads(raw)
                    info = data.get("info", {})
                    return info.get("version", "Unknown")
                    
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return "Unknown"
                elif attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    return "Unknown"
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    logger.warning(f"Failed to fetch package info for {pkg_name}: {e}")
                    return "Unknown"
        return "Unknown"

    def _check_single_package_simple(self, pkg_name: str, pkg_ver: str, ui_package_callback=None):
        """Check and update a single package."""
        if self._shutting_down.is_set() or self._check_cancelled.is_set():
            return False
        
        now = datetime.now()
        
        # Rate limiting check
        with self.lock:
            last_check = self.last_check_time.get(pkg_name)
            if last_check and now - last_check < timedelta(seconds=30):
                if ui_package_callback:
                    with self._batch_lock:
                        self._update_batch.append(pkg_name)
                        current_time = time.time()
                        if current_time - self._last_batch_update > self._batch_update_interval:
                            self._flush_batch_updates(ui_package_callback)
                            self._last_batch_update = current_time
                return True
            
            self.last_check_time[pkg_name] = now
        
        try:
            time.sleep(self._check_delay)
            
            # Fetch latest version from PyPI
            latest = self._fetch_package_info(pkg_name)
            
            # Parse versions for comparison
            def parse_version(ver):
                try:
                    parts = []
                    for part in ver.split('.'):
                        match = re.search(r'\d+', part)
                        if match:
                            parts.append(int(match.group()))
                        else:
                            parts.append(0)
                    return tuple(parts)
                except:
                    return (0,)
            
            current_ver_parsed = parse_version(pkg_ver)
            latest_ver_parsed = parse_version(latest)
            
            # Determine status
            if latest == "Unknown" or latest == "Error":
                status = "Unknown"
            elif current_ver_parsed < latest_ver_parsed:
                status = "Outdated"
            else:
                status = "Updated"
            
            # Update package data
            with self.lock:
                for i, p in enumerate(self.packages):
                    if p["name"] == pkg_name:
                        self.packages[i]["lat"] = latest
                        self.packages[i]["stat"] = status
                        break
            
            # Batch UI update
            if ui_package_callback:
                with self._batch_lock:
                    self._update_batch.append(pkg_name)
                    current_time = time.time()
                    if (current_time - self._last_batch_update > self._batch_update_interval or 
                        len(self._update_batch) >= 5):
                        self._flush_batch_updates(ui_package_callback)
                        self._last_batch_update = current_time
            
            return True
        
        except Exception as e:
            logger.warning(f"Check failed for {pkg_name}: {e}")
            with self.lock:
                for i, p in enumerate(self.packages):
                    if p["name"] == pkg_name:
                        self.packages[i]["lat"] = "Error"
                        self.packages[i]["stat"] = "Unknown"
                        break
            
            if ui_package_callback:
                with self._batch_lock:
                    self._update_batch.append(pkg_name)
                    current_time = time.time()
                    if current_time - self._last_batch_update > self._batch_update_interval:
                        self._flush_batch_updates(ui_package_callback)
                        self._last_batch_update = current_time
            
            return False

    def cancel_check(self):
        """Cancel ongoing update check."""
        self._check_cancelled.set()
        logger.info("Update check cancelled")
    
    def clear_all_cache(self):
        """Clear all caches."""
        with self.lock:
            self._search_cache.clear()
            self.last_check_time.clear()
            self.request_failures.clear()
            logger.info("All caches cleared")
    
    def save_packages_to_cache(self, environment_id: str):
        """Save packages state to cache for environment."""
        if not environment_id:
            return
        
        with self.lock:
            packages_dict = {}
            for pkg in self.packages:
                name = pkg.get("name", "")
                if name:
                    packages_dict[name] = {
                        "lat": pkg.get("lat", "Unknown"),
                        "stat": pkg.get("stat", "Unknown"),
                        "timestamp": datetime.now()
                    }
            
            self._packages_cache[environment_id] = {
                "packages": packages_dict,
                "timestamp": datetime.now()
            }
            self._trim_cache(self._packages_cache, self._packages_cache_max_size)
    
    def load_packages_with_cache(self, ui_callback, environment_id: str = None):
        """Load packages with cache support."""
        def load_task():
            try:
                new_packages = []
                
                # Try pip list first
                pip_cmd = self.get_pip_command()
                cmd = pip_cmd + ["list", "--format=json"]
                
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        shell=False
                    )
                    
                    if result.returncode == 0 and result.stdout:
                        packages_data = json.loads(result.stdout)
                        
                        cached_packages = {}
                        if environment_id:
                            cached_packages = self._get_cached_packages(environment_id)
                        
                        for pkg in packages_data:
                            name = pkg.get("name", "")
                            version = pkg.get("version", "Unknown")
                            
                            if name:
                                if name in cached_packages:
                                    cached = cached_packages[name]
                                    new_packages.append({
                                        "name": name,
                                        "ver": version,
                                        "lat": cached.get("lat", "Unknown"),
                                        "stat": cached.get("stat", "Unknown")
                                    })
                                else:
                                    new_packages.append({
                                        "name": name,
                                        "ver": version,
                                        "lat": "Unknown",
                                        "stat": "Unknown"
                                    })
                        
                        logger.info(f"Loaded {len(new_packages)} packages")
                    else:
                        new_packages = self._load_packages_via_importlib(environment_id)
                        
                except Exception:
                    new_packages = self._load_packages_via_importlib(environment_id)
                
                new_packages.sort(key=lambda x: x["name"].lower())
                
                with self.lock:
                    if not self._shutting_down.is_set():
                        self.packages = new_packages
                
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback()
                
            except Exception as e:
                logger.error(f"Load error: {e}")
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback()
        
        threading.Thread(target=load_task, daemon=True).start()
    
    def _get_cached_packages(self, environment_id: str) -> Dict:
        """Get cached packages for environment."""
        with self.lock:
            if environment_id not in self._packages_cache:
                return {}
            
            cache_entry = self._packages_cache[environment_id]
            cache_time = cache_entry.get("timestamp", datetime.min)
            
            if datetime.now() - cache_time > self._cache_expiry:
                del self._packages_cache[environment_id]
                return {}
            
            return cache_entry.get("packages", {})
    
    def _load_packages_via_importlib(self, environment_id: str = None) -> List[Dict]:
        """Fallback package loading using importlib."""
        new_packages = []
        
        try:
            try:
                from importlib.metadata import distributions
            except ImportError:
                from importlib_metadata import distributions
            
            cached_packages = {}
            if environment_id:
                cached_packages = self._get_cached_packages(environment_id)
            
            for dist in distributions():
                if self._shutting_down.is_set():
                    break
                
                name = dist.metadata.get("Name") or dist.name
                if name and self._is_valid_package_name(name):
                    version = dist.version
                    
                    if name in cached_packages:
                        cached = cached_packages[name]
                        new_packages.append({
                            "name": name,
                            "ver": version,
                            "lat": cached.get("lat", "Unknown"),
                            "stat": cached.get("stat", "Unknown")
                        })
                    else:
                        new_packages.append({
                            "name": name,
                            "ver": version,
                            "lat": "Unknown",
                            "stat": "Unknown"
                        })
            
            logger.info(f"Loaded {len(new_packages)} packages via importlib")
            
        except Exception as e:
            logger.error(f"Importlib fallback failed: {e}")
        
        return new_packages
    
    def set_pip_command(self, pip_cmd: List[str]):
        """Set pip command for current environment."""
        with self.lock:
            if pip_cmd and isinstance(pip_cmd, list):
                self.pip_command = pip_cmd.copy()
                logger.info(f"Set pip command: {' '.join(pip_cmd)}")
            else:
                self.pip_command = [sys.executable, "-m", "pip"]
    
    def get_pip_command(self) -> List[str]:
        """Get current pip command."""
        with self.lock:
            return self.pip_command or [sys.executable, "-m", "pip"]
    
    def search_pypi_packages(self, search_term: str, ui_callback):
        """Search for packages on PyPI."""
        if self._shutting_down.is_set():
            if ui_callback:
                ui_callback([])
            return
        
        search_term = search_term.strip()
        if not search_term:
            if ui_callback:
                ui_callback([])
            return
        
        def search_task():
            try:
                results = []
                
                # Try JSON API first
                json_results = self._search_json_api(search_term)
                if json_results:
                    results = json_results
                else:
                    web_results = self._search_web_scrape(search_term)
                    if web_results:
                        results = web_results
                
                processed = self._process_search_results(results) if not self._shutting_down.is_set() else []
                
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback(processed)
                    
            except Exception as e:
                logger.error(f"Search failed: {e}")
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback([])
        
        threading.Thread(target=search_task, daemon=True).start()
    
    def _search_json_api(self, search_term: str) -> list:
        """Search using PyPI JSON API."""
        if self._shutting_down.is_set():
            return []
        
        try:
            url = f"https://pypi.org/pypi/{urllib.parse.quote(search_term.strip().lower())}/json"
            
            try:
                response = urllib.request.urlopen(url, timeout=10)
                raw = response.read(self.MAX_JSON_SIZE + 1)
                
                if len(raw) > self.MAX_JSON_SIZE:
                    return []
                
                data = json.loads(raw)
                info = data.get("info", {})
                
                name = info.get("name", "").strip()
                version = info.get("version", "Unknown")
                summary = info.get("summary", "No description")
                
                if not name or not version:
                    return []
                
                return [{
                    "name": name,
                    "version": version,
                    "summary": summary
                }]
                
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return self._search_json_search_api(search_term)
                return []
            except Exception:
                return []
            
        except Exception:
            return []
    
    def _search_json_search_api(self, search_term: str) -> list:
        """Search using PyPI search endpoint."""
        try:
            encoded = urllib.parse.quote(search_term)
            url = f"https://pypi.org/search/?q={encoded}&format=json"
            
            try:
                response = urllib.request.urlopen(url, timeout=10)
                raw = response.read(self.MAX_JSON_SIZE + 1)
                
                if len(raw) > self.MAX_JSON_SIZE:
                    return []
                
                data = json.loads(raw)
                results = []
                
                for item in data.get('projects', [])[:20]:
                    results.append({
                        "name": str(item.get('name', '')).strip(),
                        "version": str(item.get('version', 'Unknown')),
                        "summary": str(item.get('description', ''))
                    })
                
                return results
            except Exception:
                return []
        except Exception:
            return []
    
    def _search_web_scrape(self, search_term: str) -> list:
        """Web scraping fallback for search."""
        try:
            encoded = urllib.parse.quote(search_term)
            url = f"https://pypi.org/search/?q={encoded}"
            
            try:
                response = urllib.request.urlopen(url, timeout=10)
                html = response.read().decode('utf-8', errors='ignore')
                
                pattern = re.compile(
                    r'<span[^>]*class="[^"]*package-snippet__name[^"]*"[^>]*>([^<]{1,100})</span>'
                    r'.*?<span[^>]*class="[^"]*package-snippet__version[^"]*"[^>]*>([^<]{1,50})</span>',
                    re.DOTALL
                )
                
                results = []
                seen = set()
                
                for match in pattern.finditer(html):
                    name = match.group(1).strip()
                    version = match.group(2).strip() if match.group(2) else 'Unknown'
                    
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        results.append({
                            "name": name,
                            "version": version,
                            "summary": "No description available"
                        })
                
                return results[:50]
                
            except Exception:
                return []
        except Exception:
            return []
    
    def _process_search_results(self, raw_results: list) -> list:
        """Process search results and mark installed packages."""
        if not raw_results:
            return []
        
        with self.lock:
            local_packages = {}
            for p in self.packages:
                local_packages[p["name"].lower()] = {
                    "name": p["name"],
                    "version": p["ver"],
                    "latest": p["lat"],
                    "status": p["stat"]
                }
        
        processed = []
        seen = set()
        
        for result in raw_results:
            name = result.get("name", "").strip()
            if not name:
                continue
            
            name_lower = name.lower()
            if name_lower in seen:
                continue
            seen.add(name_lower)
            
            local_info = local_packages.get(name_lower)
            is_installed = local_info is not None
            installed_version = local_info["version"] if local_info else None
            latest_version = local_info["latest"] if local_info else result.get("version", "Unknown")
            
            processed.append({
                "name": name,
                "version": result.get("version", "Unknown"),
                "summary": (result.get("summary", "")[:150] + "...") if len(result.get("summary", "")) > 150 else result.get("summary", ""),
                "installed": is_installed,
                "installed_version": installed_version,
                "latest_version": latest_version
            })
        
        processed.sort(key=lambda x: x["name"].lower())
        return processed
    
    def install_pypi_package(self, package_name: str, version: str = None, 
                           ui_callback=None, progress_callback=None):
        """Install package from PyPI."""
        if self._shutting_down.is_set():
            if ui_callback:
                ui_callback(False, "Shutting down")
            return
        
        def install_task():
            try:
                install_cmd = ["install"]
                if version and version != "Unknown":
                    install_cmd.append(f"{package_name}=={version}")
                else:
                    install_cmd.append(package_name)
                
                pip_cmd = self.get_pip_command()
                
                success, message, _ = run_pip_with_real_progress(
                    install_cmd, 
                    progress_callback=progress_callback,
                    pip_cmd=pip_cmd
                )
                
                if not success:
                    raise Exception(message)
                
                self.clear_rate_limit(package_name)
                self._update_after_install(package_name, pip_cmd)
                
                with self.lock:
                    self._search_cache.clear()
                
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback(True, f"Installed {package_name}")
                    
            except Exception as e:
                logger.error(f"Install failed: {e}")
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback(False, str(e))
        
        threading.Thread(target=install_task, daemon=True).start()
    
    def _update_after_install(self, package_name: str, pip_cmd: List[str]):
        """Update package info after installation."""
        try:
            cmd = pip_cmd + ["show", package_name]
            
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False
                )
                
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if line.startswith("Version:"):
                            installed_version = line.split(":", 1)[1].strip()
                            latest_version = self._fetch_package_info(package_name)
                            
                            status = "Updated" if installed_version == latest_version else "Outdated"
                            
                            with self.lock:
                                for i, p in enumerate(self.packages):
                                    if p["name"].lower() == package_name.lower():
                                        self.packages[i]["ver"] = installed_version
                                        self.packages[i]["lat"] = latest_version
                                        self.packages[i]["stat"] = status
                                        break
                                else:
                                    self.packages.append({
                                        "name": package_name,
                                        "ver": installed_version,
                                        "lat": latest_version,
                                        "stat": status
                                    })
                                    self.packages.sort(key=lambda x: x["name"].lower())
                            
                            return
                
            except Exception:
                pass
            
            # Fallback to importlib
            try:
                from importlib.metadata import distribution, PackageNotFoundError
                dist = distribution(package_name)
                installed_version = dist.version
                latest_version = self._fetch_package_info(package_name)
                
                status = "Updated" if installed_version == latest_version else "Outdated"
                
                with self.lock:
                    for i, p in enumerate(self.packages):
                        if p["name"].lower() == package_name.lower():
                            self.packages[i]["ver"] = installed_version
                            self.packages[i]["lat"] = latest_version
                            self.packages[i]["stat"] = status
                            break
                    else:
                        self.packages.append({
                            "name": package_name,
                            "ver": installed_version,
                            "lat": latest_version,
                            "stat": status
                        })
                        self.packages.sort(key=lambda x: x["name"].lower())
                
            except PackageNotFoundError:
                pass
            except Exception:
                pass
                
        except Exception as e:
            logger.error(f"Error updating package info: {e}")
    
    def load_packages(self, ui_callback):
        """Load packages (legacy method)."""
        self.load_packages_with_cache(ui_callback, None)
    
    def refresh_packages_data(self):
        """Get current package data."""
        with self.lock:
            packages_copy = list(self.packages)
            total = len(packages_copy)
            outdated = sum(1 for p in packages_copy if p["stat"] == "Outdated")
        
        return packages_copy, total, outdated
    
    def filter_packages(self, mode="All"):
        """Filter packages by status."""
        with self.lock:
            packages_copy = list(self.packages)
        
        if mode == "All":
            return packages_copy
        elif mode == "Outdated":
            return [p for p in packages_copy if p["stat"] == "Outdated"]
        elif mode == "Updated":
            return [p for p in packages_copy if p["stat"] == "Updated"]
        else:
            return []
    
    def search_packages(self, term):
        """Search local packages by name."""
        term_lower = term.lower()
        with self.lock:
            return [p for p in self.packages if term_lower in p["name"].lower()]
    
    def get_package_by_name(self, package_name: str):
        """Get package by name."""
        with self.lock:
            for p in self.packages:
                if p["name"] == package_name:
                    return p.copy()
        return None
    
    def update_package_status(self, pkg_name: str, new_version: str, 
                            latest_version: str, status: str = "Updated"):
        """Update package status."""
        with self.lock:
            for i, p in enumerate(self.packages):
                if p["name"] == pkg_name:
                    self.packages[i]["ver"] = new_version
                    self.packages[i]["lat"] = latest_version
                    self.packages[i]["stat"] = status
                    break
    
    def clear_rate_limit(self, pkg_name: str):
        """Clear rate limiting for package."""
        with self.lock:
            self.last_check_time.pop(pkg_name, None)
            self.request_failures.pop(pkg_name, None)
    
    def finish_check(self):
        """Reset checking state."""
        with self.lock:
            self.checking = False
    
    def _is_valid_package_name(self, name):
        """Validate package name format."""
        return bool(re.match(r'^[a-zA-Z0-9._-]+$', name))
    
    def set_shutting_down(self, value: bool):
        """Set shutdown flag."""
        if value:
            self._shutting_down.set()
        else:
            self._shutting_down.clear()
    
    def is_shutting_down(self) -> bool:
        """Check if shutting down."""
        return self._shutting_down.is_set()