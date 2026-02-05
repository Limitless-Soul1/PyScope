"""PyScope:https://github.com/Limitless-Soul1/PyScope"""

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
from typing import List, Dict
from collections import OrderedDict

from .utils import logger, run_pip_with_real_progress
from .system import get_detector

detector = get_detector()

class PackageManagerCore:
    """Core package management with thread safety, rate limiting, and caching."""
    
    def __init__(self):
        self.lock = threading.RLock()
        self._shutting_down = threading.Event()
        self._search_cancel = threading.Event()
        
        self.packages: list = []
        self.checking = False
        
        # Generation counter for load requests (Prevents race conditions)
        self._load_generation = 0
        self._load_gen_lock = threading.Lock()
        
        self.last_check_time = {}
        self.request_failures = {}
        
        # LRU cache implementation
        self._search_cache = OrderedDict()
        self._search_cache_ttl = timedelta(minutes=5)
        self._search_cache_max_size = 100
        
        self._packages_cache = OrderedDict()
        self._cache_expiry = timedelta(hours=1)
        self._packages_cache_max_size = 20
        
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pyscope")

        # Security limits
        self.MAX_SEARCH_LENGTH = 100
        self.MAX_JSON_SIZE = 50_000_000
        
        # Environment support
        self.pip_command = None
        
        # Check cancellation
        self._check_cancelled = threading.Event()
        
        # Network failure tracking
        self._consecutive_failures = 0
        self._failures_lock = threading.Lock()
        self._consecutive_failures_threshold = 10
        
        # Performance parameters
        self._check_delay = 0.1
        self._update_batch = []
        self._batch_lock = threading.RLock()
        self._last_batch_update = 0
        self._batch_update_interval = 0.3
        self._thread_semaphore = threading.Semaphore(4)
        
        self.signals = None
        self.current_environment_id = None
        
        # Periodic background save
        self._start_background_saver()
        logger.info("PackageManagerCore initialized")

    def _start_background_saver(self):
        """Background thread to save package cache."""
        def saver_loop():
            while not self._shutting_down.is_set():
                for _ in range(60): 
                    if self._shutting_down.is_set(): return
                    time.sleep(1)
                
                with self.lock:
                    if self.current_environment_id and self.packages:
                        self._save_packages_to_cache(self.current_environment_id, self.packages)
        
        threading.Thread(target=saver_loop, daemon=True, name="pyscope-saver").start()

    def shutdown(self, timeout=2.0):
        """Gracefully shutdown the package manager."""
        logger.info("Shutting down PackageManagerCore...")
        self._shutting_down.set()
        self._check_cancelled.set()
        
        if hasattr(self, '_executor') and self._executor:
            with self.lock:
                if hasattr(self, '_active_futures'):
                    for future in list(self._active_futures):
                        try: future.cancel()
                        except: pass
                    self._active_futures.clear()
            
            try:
                self._executor.shutdown(wait=False)
            except Exception as e:
                logger.warning(f"Shutdown error: {e}")
                
        logger.info("PackageManagerCore shutdown complete")

    def _trim_cache(self, cache: OrderedDict, max_size: int):
        """Trim cache to maintain maximum size limit."""
        while len(cache) > max_size:
            cache.popitem(last=False)

    def check_updates(self, ui_start_callback=None, ui_finish_callback=None, ui_package_callback=None):
        """Check for updates for all installed packages."""
        if self._shutting_down.is_set():
            logger.info("Shutting down, skipping update check")
            if ui_finish_callback: ui_finish_callback()
            if self.signals: self.signals.check_finished.emit()
            return
        
        logger.info("Update check requested")
        
        # Internal wrappers for signals + callbacks
        def _on_start():
            if ui_start_callback: ui_start_callback()
            if self.signals: self.signals.check_started.emit()

        def _on_finish():
            if ui_finish_callback: ui_finish_callback()
            try:
                if self.signals: self.signals.check_finished.emit()
            except RuntimeError:
                pass # Signal source deleted
            
        def _on_package(pkg_name):
            if ui_package_callback: ui_package_callback(pkg_name)
            if self.signals: self.signals.package_updated.emit(pkg_name)

        with self.lock:
            if self.checking:
                logger.info("Already checking updates, skipping")
                _on_finish()
                return
            self.checking = True
            self._check_cancelled.clear()
        
        try:
            _on_start()
            
            check_thread = threading.Thread(
                target=self._check_updates_safe_parallel,
                args=(_on_finish, _on_package),
                daemon=True
            )
            check_thread.start()
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
            
            global_timeout = 300
            start_time = time.time()
            max_workers = 4
            
            logger.info(f"Starting parallel update check for {total} packages")
            import threading
            logger.info(f"Active threads before check: {threading.active_count()}")
            
            # Limit pending tasks
            task_semaphore = threading.Semaphore(50)
            
            # Reset batch system
            with self._batch_lock:
                self._update_batch = []
                self._last_batch_update = time.time()
            
            # Use persistent executor
            future_to_package = {}
            active_futures = []
            
            for idx, (pkg_name, pkg_ver) in enumerate(packages_to_check):
                if self._shutting_down.is_set() or self._check_cancelled.is_set():
                    logger.info("Update check cancelled during task submission")
                    break
                
                # Sleep less frequently for faster overall checks
                if idx % 10 == 0 and idx > 0:
                    time.sleep(self._check_delay)  # Removed *3 multiplier
                
                # Acquire semaphore (blocks if too many tasks are pending)
                task_semaphore.acquire()
                
                # Track if we need to release manually on failure
                submitted = False
                try:
                    if self._shutting_down.is_set():
                        task_semaphore.release()
                        break
                        
                    future = self._executor.submit(
                        self._check_single_package_simple, 
                        pkg_name, pkg_ver, ui_package_callback
                    )
                    submitted = True # From here on, on_future_done handles release
                    
                    # Track future for cancellation on shutdown
                    with self.lock:
                        if not hasattr(self, '_active_futures'):
                            self._active_futures = set()
                        self._active_futures.add(future)
                    
                    # Robust callback to release semaphore AND remove from tracking
                    def on_future_done(f):
                        task_semaphore.release()
                        with self.lock:
                            if hasattr(self, '_active_futures'):
                                self._active_futures.discard(f)
                    
                    future.add_done_callback(on_future_done)
                    
                    future_to_package[future] = (pkg_name, pkg_ver)
                    active_futures.append(future)
                    
                except Exception as e:
                    if not submitted:
                        task_semaphore.release()
                    logger.error(f"Failed to submit task or register callback: {e}")
                    if isinstance(e, RuntimeError):
                        break
            
            # Process results as they complete
            try:
                for future in as_completed(active_futures, timeout=global_timeout):
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
                            # Cancel remaining
                            for f in active_futures:
                                f.cancel()
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
                if self.current_environment_id and not self._shutting_down.is_set():
                    self._save_packages_to_cache(self.current_environment_id, self.packages)
            
            # CRITICAL: Flush any remaining batch updates BEFORE calling finish callback
            # This ensures all status updates are visible before "completed" message
            self._flush_batch_updates(ui_package_callback)
            
            logger.info(f"Update check finished (cleanup). Time: {time.time() - start_time:.2f}s")
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
                logger.info(f"Checking single package task: {pkg_name}")
                
                with self.lock:
                    package_info = None
                    current_version = "Unknown"
                    for p in self.packages:
                        if p["name"] == pkg_name:
                            package_info = p.copy()
                            current_version = p.get("ver", "Unknown")
                            break
                
                if not package_info:
                    # Try to discover it via pip show in TARGET environment
                    try:
                        cmd = self.pip_command + ["show", pkg_name]
                        # Safe subprocess execution
                        result = subprocess.run(
                            cmd, 
                            capture_output=True, 
                            text=True, 
                            timeout=5,
                            encoding='utf-8', 
                            errors='replace'
                        )
                        
                        if result.returncode == 0:
                            current_version = "Unknown"
                            # Parse version from output
                            for line in result.stdout.splitlines():
                                if line.startswith("Version:"):
                                    current_version = line.split(":", 1)[1].strip()
                                    break
                            
                            logger.info(f"Discovered new package {pkg_name} v{current_version} in target env")
                            with self.lock:
                                new_pkg = {
                                    "name": pkg_name, 
                                    "ver": current_version, 
                                    "lat": "Unknown", 
                                    "stat": "Unknown"
                                }
                                # Check again in case race condition added it
                                if not any(p["name"] == pkg_name for p in self.packages):
                                    self.packages.append(new_pkg)
                                    self.packages.sort(key=lambda x: x["name"].lower())
                                package_info = new_pkg
                        else:
                             logger.warning(f"Package {pkg_name} not found via pip show")
                             if callback:
                                 callback(False, f"Package {pkg_name} not found in target environment")
                             return

                    except Exception as e:
                        logger.warning(f"Package discovery failed for {pkg_name}: {e}")
                        if callback:
                            callback(False, f"Error verifying package {pkg_name}: {e}")
                        return
                
                # Force check since this is a manual user request
                logger.info(f"Calling _check_single_package_simple for {pkg_name}...")
                success = self._check_single_package_simple(pkg_name, current_version, None, force=True)
                logger.info(f"_check_single_package_simple returned {success}")
                
                with self.lock:
                    updated_info = None
                    for p in self.packages:
                        if p["name"] == pkg_name:
                            updated_info = p.copy()
                            break
                
                if updated_info:
                    logger.info(f"Single package check completed for {pkg_name}: {updated_info['stat']}")
                    if callback:
                        if success:
                            logger.info("Calling success callback...")
                            callback(True, None)
                        else:
                            callback(False, f"Failed to fetch latest version for {pkg_name}")
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
            logger.info(f"Wait for semaphore for {pkg_name}...")
            with self._thread_semaphore:
                logger.info(f"Acquired semaphore for {pkg_name}, starting checks...")
                check_task()
                logger.info(f"Released semaphore for {pkg_name}")
        
        t = threading.Thread(target=task_with_semaphore, daemon=True)
        t.start()
        logger.info(f"Started thread for {pkg_name}")

    def _fetch_package_info(self, pkg_name: str):
        """Fetch package information from PyPI with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            # Check for cancellation before each attempt
            if self._shutting_down.is_set() or self._check_cancelled.is_set():
                return "Unknown"
                
            try:
                url = f"https://pypi.org/pypi/{pkg_name}/json"
                req = urllib.request.Request(url, headers={'User-Agent': 'PyScope'})
                
                context = ssl.create_default_context()
                # Enforce size limit with explicit read
                with closing(urllib.request.urlopen(req, timeout=10, context=context)) as response:
                    # Read only first 50MB to prevent DoS
                    raw = response.read(50_000_000) 
                    
                    if len(raw) >= 50_000_000:
                        raise ValueError("Response too large")

                    
                    data = json.loads(raw)
                    info = data.get("info", {})
                    return info.get("version", "Unknown")
                    
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return "Unknown"
                elif attempt < max_retries - 1:
                    if self._shutting_down.is_set() or self._check_cancelled.is_set(): return "Unknown"
                    time.sleep(1)
                    continue
                else:
                    return "Unknown"
            except Exception as e:
                if attempt < max_retries - 1:
                    if self._shutting_down.is_set() or self._check_cancelled.is_set(): return "Unknown"
                    time.sleep(1)
                    continue
                else:
                    logger.warning(f"Failed to fetch package info for {pkg_name}: {e}")
                    return "Unknown"
        return "Unknown"

    def _check_single_package_simple(self, pkg_name: str, pkg_ver: str, ui_package_callback=None, force=False):
        """Check and update a single package."""
        if self._shutting_down.is_set() or self._check_cancelled.is_set():
            return False
        
        now = datetime.now()
        
        # Rate limiting check (Skip if forced)
        if not force:
            with self.lock:
                last_check = self.last_check_time.get(pkg_name)
                # Find current status to allow retry if Unknown
                current_status = "Unknown"
                for p in self.packages:
                    if p["name"] == pkg_name:
                        current_status = p.get("stat", "Unknown")
                        break
                
                # Skip rate limiting check if status is Unknown (allow retry)
                if current_status != "Unknown" and last_check and now - last_check < timedelta(seconds=30):
                    if ui_package_callback:
                        try:
                            ui_package_callback(pkg_name)
                        except: pass
                    return True
                self.last_check_time[pkg_name] = now
        
        try:
            time.sleep(self._check_delay)
            
            # Fetch latest version from PyPI
            latest = self._fetch_package_info(pkg_name)
            
            # Determine status
            try:
                from .utils import VersionComparator
                comparator = VersionComparator()
                
                if latest == "Unknown" or latest == "Error":
                    status = "Unknown"
                elif comparator.is_outdated(pkg_ver, latest):
                    status = "Outdated"
                else:
                    status = "Updated"
            except ImportError:
                # Fallback
                status = "Unknown"
                if latest != "Unknown":
                    # Simple normalization for fallback
                    status = "Updated" if str(latest).strip().lower() == str(pkg_ver).strip().lower() else "Outdated"
            
            # Update data before callback
            updated = False
            with self.lock:
                for i, p in enumerate(self.packages):
                    if p["name"] == pkg_name:
                        self.packages[i]["lat"] = latest
                        self.packages[i]["stat"] = status
                        updated = True
                        break
            
            # Call UI callback AFTER updating data
            if updated:
                if ui_package_callback:
                    try:
                        ui_package_callback(pkg_name)
                    except Exception as e:
                        logger.error(f"Callback error for {pkg_name}: {e}")
                
                if self.signals:
                    self.signals.package_updated.emit(pkg_name)
            
            return True
            
        except Exception as e:
            logger.warning(f"Check failed for {pkg_name}: {e}")
            # Still update with error status
            updated = False
            with self.lock:
                for i, p in enumerate(self.packages):
                    if p["name"] == pkg_name:
                        self.packages[i]["lat"] = "Error"
                        self.packages[i]["stat"] = "Unknown"
                        updated = True
                        break
            
            if updated:
                if ui_package_callback:
                    try:
                        ui_package_callback(pkg_name)
                    except Exception as e:
                        logger.error(f"Callback error for {pkg_name}: {e}")
                
                if self.signals:
                    self.signals.package_updated.emit(pkg_name)
            
            return False


    def cancel_check(self):
        """Cancel ongoing update check."""
        self._check_cancelled.set()
        with self.lock:
            self.checking = False
        logger.info("Update check cancelled")
    
    def clear_all_cache(self):
        """Clear all caches."""
        with self.lock:
            self._search_cache.clear()
            self.last_check_time.clear()
            self.request_failures.clear()
            logger.info("All caches cleared")
    
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

    def _save_packages_to_cache(self, environment_id: str, packages: List[Dict]):
        """Save packages state to cache for environment."""
        if not environment_id:
            return
        
        with self.lock:
            packages_dict = {}
            for pkg in packages:
                name = pkg.get("name", "")
                if name:
                    packages_dict[name] = {
                        "ver": pkg.get("ver", "Unknown"),  # Save version for comparison
                        "lat": pkg.get("lat", "Unknown"),
                        "stat": pkg.get("stat", "Unknown"),
                        "timestamp": datetime.now()
                    }
            
            self._packages_cache[environment_id] = {
                "packages": packages_dict,
                "timestamp": datetime.now()
            }
            self._trim_cache(self._packages_cache, self._packages_cache_max_size)
    
    def load_packages_with_cache(self, ui_callback, environment_id: str = None, force_refresh: bool = False):
        """Load packages with cache support."""
        # Cancel pending operations
        self._check_cancelled.set()
        time.sleep(0.05)
        self._check_cancelled.clear()
        
        self.current_environment_id = environment_id
        
        if force_refresh:
            with self.lock:
                self._packages_cache.clear()
                logger.info("Forced cache clear")
        
        # Increment generation to invalidate previous pending loads
        with self._load_gen_lock:
            self._load_generation += 1
            current_generation = self._load_generation
        
        def load_task():
            try:
                # Check generation before starting
                with self._load_gen_lock:
                    if self._load_generation != current_generation:
                        logger.info("Load task cancelled (stale generation)")
                        return

                new_packages = []
                
                # Determine if we are targeting the HOST environment
                # If target python is same as running python, we can use introspection
                target_python = self.get_python_command()
                if isinstance(target_python, list): target_python = target_python[0] # Handle list case just in case
                
                is_host_env = False
                try:
                    if os.path.realpath(target_python) == os.path.realpath(sys.executable):
                        is_host_env = True
                except: pass
                
                # Use parallel strategies
                packages_from_pip = self._try_pip_list()
                packages_from_importlib = []
                
                # Introspection only for host environment
                if is_host_env:
                    packages_from_importlib = self._try_importlib()
                else:
                    logger.info("Target is external environment: Skipping importlib/site-packages strategies")
                
                # Check generation before merging
                with self._load_gen_lock:
                    if self._load_generation != current_generation:
                        return
                
                # Merge results
                all_packages = {}
                
                for pkg in packages_from_pip + packages_from_importlib:
                    name = pkg.get("name", "").lower()
                    if name and name not in all_packages:
                        all_packages[name] = pkg
                
                # Convert to list
                new_packages = list(all_packages.values())
                
                # Apply cache if available
                cached_packages = {}
                if environment_id:
                    cached_packages = self._get_cached_packages(environment_id)
                
                # Update with cache info (lat, stat)
                for pkg in new_packages:
                    name = pkg["name"].lower()
                    if name in cached_packages:
                        cached = cached_packages[name]
                        pkg["lat"] = cached.get("lat", "Unknown")
                        pkg["stat"] = cached.get("stat", "Unknown")
                
                new_packages.sort(key=lambda x: x["name"].lower())
                
                # Final generation check before committing state
                with self.lock:
                    with self._load_gen_lock:
                        if self._load_generation != current_generation:
                            logger.info("Load task result discarded (stale generation)")
                            return
                    
                    # CRITICAL: Preserve statuses/latest versions right before commit
                    # This avoids race conditions with asynchronous update checks
                    if environment_id == self.current_environment_id:
                        # 1. Get current in-memory status
                        current_states = {p["name"].lower(): {"ver": p.get("ver"), "lat": p.get("lat"), "stat": p.get("stat")} for p in self.packages}
                        
                        # 2. Get status from disk cache (if memory is empty/Unknown)
                        disk_cache = self._get_cached_packages(environment_id)
                        
                        for pkg in new_packages:
                            name = pkg["name"].lower()
                            
                            # Priority 1: Current In-Memory State
                            curr = current_states.get(name)
                            # Priority 2: Disk Cache
                            cached = disk_cache.get(name)
                            
                            best_lat = "Unknown"
                            best_stat = "Unknown"
                            
                            if curr and curr.get("ver") == pkg["ver"]:
                                if curr["lat"] not in (None, "Unknown"):
                                    best_lat = curr["lat"]
                                    best_stat = curr["stat"]
                            
                            # Check Disk Cache second (if memory didn't yield result)
                            elif cached and cached.get("ver") == pkg["ver"]:
                                if cached.get("lat") not in (None, "Unknown"):
                                    best_lat = cached["lat"]
                                    best_stat = cached.get("stat", "Unknown")
                            
                            # Smart Inference: If version matches known latest -> Updated
                            elif cached and cached.get("lat") == pkg["ver"]:
                                best_lat = cached["lat"]
                                best_stat = "Updated"
                            
                            # Apply best found state if currently unknown
                            if pkg.get("lat") == "Unknown" and best_lat != "Unknown":
                                pkg["lat"] = best_lat
                            if pkg.get("stat") == "Unknown" and best_stat != "Unknown":
                                pkg["stat"] = best_stat

                    if not self._shutting_down.is_set():
                        self.packages = new_packages
                        # Only save to cache if we actually have some data to preserve
                        if environment_id:
                            self._save_packages_to_cache(environment_id, new_packages)
                
                logger.info(f"Loaded {len(new_packages)} packages successfully")
                
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback()
                
            except Exception as e:
                logger.error(f"Load error: {e}")
                if not self._shutting_down.is_set() and ui_callback:
                    ui_callback()
        
        threading.Thread(target=load_task, daemon=True).start()

    def _try_pip_list(self):
        """Try to get packages via pip list - multiple formats"""
        packages = []
        pip_cmd = self.get_pip_command()
        
        # Try different formats
        formats = [
            ["list", "--format", "json"],
            ["list", "--format=json"],
            ["freeze"]
        ]
        
        for fmt in formats:
            try:
                cmd = pip_cmd + fmt
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    shell=False
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    if fmt[0] == "freeze":
                        # Parse freeze format
                        for line in result.stdout.strip().split('\n'):
                            if '==' in line:
                                name, version = line.split('==', 1)
                                packages.append({
                                    "name": name.strip(),
                                    "ver": version.strip(),
                                    "lat": "Unknown",
                                    "stat": "Unknown"
                                })
                    else:
                        # Parse JSON format
                        data = json.loads(result.stdout)
                        for pkg in data:
                            packages.append({
                                "name": pkg.get("name", ""),
                                "ver": pkg.get("version", "Unknown"),
                                "lat": "Unknown",
                                "stat": "Unknown"
                            })
                    
                    logger.info(f"Got {len(packages)} packages via pip {' '.join(fmt)}")
                    break  # Stop at first success
                    
            except Exception as e:
                logger.debug(f"pip {' '.join(fmt)} failed: {e}")
                continue
        
        return packages

    def _try_importlib(self):
        """Try to get packages via importlib - robust version"""
        packages = []
        
        try:
            # Method 1: importlib.metadata (Python 3.8+)
            try:
                from importlib.metadata import distributions
                for dist in distributions():
                    try:
                        name = dist.metadata.get("Name") or dist.name
                        if name and self._is_valid_package_name(name):
                            packages.append({
                                "name": name,
                                "ver": dist.version,
                                "lat": "Unknown",
                                "stat": "Unknown"
                            })
                    except:
                        continue
                logger.info(f"Got {len(packages)} packages via importlib.metadata")
                return packages
            except ImportError:
                pass
            
            # Method 2: importlib_metadata (backport)
            try:
                from importlib_metadata import distributions
                for dist in distributions():
                    try:
                        name = dist.metadata.get("Name") or dist.name
                        if name and self._is_valid_package_name(name):
                            packages.append({
                                "name": name,
                                "ver": dist.version,
                                "lat": "Unknown",
                                "stat": "Unknown"
                            })
                    except:
                        continue
                logger.info(f"Got {len(packages)} packages via importlib_metadata")
                return packages
            except ImportError:
                pass
            
            # Method 3: pkg_resources (legacy)
            try:
                import pkg_resources
                for dist in pkg_resources.working_set:
                    packages.append({
                        "name": dist.key,
                        "ver": dist.version,
                        "lat": "Unknown",
                        "stat": "Unknown"
                    })
                logger.info(f"Got {len(packages)} packages via pkg_resources")
                return packages
            except ImportError:
                pass
            
            # Method 4: Scan site-packages directly
            packages = self._scan_site_packages()
            
        except Exception as e:
            logger.error(f"All importlib methods failed: {e}")
        
        return packages

    def _scan_site_packages(self):
        """Scan site-packages directory as last resort"""
        packages = []
        try:
            import site
            import os
            import glob
            
            try:
                site_packages = site.getsitepackages()
            except AttributeError:
                site_packages = [site.getusersitepackages()]
            
            for path in site_packages:
                if not os.path.exists(path):
                    continue
                    
                egg_info_dirs = os.path.join(path, "*.dist-info")
                for dist_info in glob.glob(egg_info_dirs):
                    try:
                        metadata_path = os.path.join(dist_info, "METADATA")
                        if os.path.exists(metadata_path):
                            with open(metadata_path, 'r', encoding='utf-8', errors='ignore') as f:
                                name = None
                                version = None
                                for line in f:
                                    if line.startswith("Name:"):
                                        name = line.split(":", 1)[1].strip()
                                    elif line.startswith("Version:"):
                                        version = line.split(":", 1)[1].strip()
                                    if name and version:
                                        break
                                
                                if name and version:
                                    packages.append({
                                        "name": name,
                                        "ver": version,
                                        "lat": "Unknown",
                                        "stat": "Unknown"
                                    })
                    except:
                        continue
            
            logger.info(f"Got {len(packages)} packages from site-packages")
        except Exception as e:
            logger.error(f"Site-packages scan failed: {e}")
        
        return packages

    def debug_environment(self):
        """Debug function to check environment issues"""
        logger.info("=== DEBUG ENVIRONMENT ===")
        
        # 1. Check Python path
        python_cmd = self.get_pip_command()[0]
        logger.info(f"Python executable: {python_cmd}")
        
        # 2. Check pip availability
        try:
            result = subprocess.run(
                [python_cmd, "-m", "pip", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info(f"Pip version: {result.stdout.strip() if result.returncode == 0 else 'NOT AVAILABLE'}")
        except Exception as e:
            logger.error(f"Pip check failed: {e}")
        
        # 3. Check actual installed packages
        try:
            # Direct import check
            import pkgutil
            modules = list(pkgutil.iter_modules())
            logger.info(f"Found {len(modules)} modules via pkgutil")
        except Exception as e:
            logger.error(f"Module check failed: {e}")
    
    def set_pip_command(self, pip_cmd: List[str]):
        """Set pip command for current environment."""
        with self.lock:
            if pip_cmd and isinstance(pip_cmd, list):
                self.pip_command = pip_cmd.copy()
                logger.info(f"Set pip command: {' '.join(pip_cmd)}")
            else:
                # Use detector to get actual Python (handles frozen mode)
                self.pip_command = [detector.get_actual_python_executable(), "-m", "pip"]
    
    def get_pip_command(self) -> List[str]:
        """Get current pip command."""
        with self.lock:
            if self.pip_command:
                return self.pip_command
            # Fallback: use detector (handles frozen mode)
            return [detector.get_actual_python_executable(), "-m", "pip"]

    def get_python_command(self) -> List[str]:
        """Get current python command."""
        with self.lock:
            if self.pip_command and len(self.pip_command) > 0:
                return [self.pip_command[0]]
            # Fallback: use detector (handles frozen mode)
            return [detector.get_actual_python_executable()]
    
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
                # Try with standard verification first
                try:
                    response = urllib.request.urlopen(url, timeout=10)
                except ssl.SSLError:
                     # Fail securely, do not bypass SSL
                    raise
                except Exception:
                    raise

                
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
                try:
                    response = urllib.request.urlopen(url, timeout=10)
                except Exception:
                    return []

                
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
                try:
                    response = urllib.request.urlopen(url, timeout=10)
                except Exception:
                    return []

                
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
        
        def internal_progress_callback(data):
            if progress_callback:
                progress_callback(data)
            if self.signals:
                self.signals.operation_progress.emit(data)

        def install_task():
            if self.signals:
                self.signals.operation_started.emit("install", package_name)
            
            try:
                install_cmd = ["install"]
                if version and version != "Unknown":
                    install_cmd.append(f"{package_name}=={version}")
                else:
                    install_cmd.append(package_name)
                
                pip_cmd = self.get_pip_command()
                
                success, message, _ = run_pip_with_real_progress(
                    install_cmd, 
                    progress_callback=internal_progress_callback,
                    pip_cmd=pip_cmd
                )
                
                if not success:
                    raise Exception(message)
                
                self.clear_rate_limit(package_name)
                self._update_after_install(package_name, pip_cmd)
                
                with self.lock:
                    self._search_cache.clear()
                
                if not self._shutting_down.is_set():
                    if ui_callback:
                        ui_callback(True, f"Installed {package_name}")
                    if self.signals:
                        self.signals.operation_completed.emit(True, f"Installed {package_name}")
                    
            except Exception as e:
                logger.error(f"Install failed: {e}")
                if not self._shutting_down.is_set():
                    if ui_callback:
                        ui_callback(False, str(e))
                    if self.signals:
                        self.signals.operation_completed.emit(False, str(e))
        
        threading.Thread(target=install_task, daemon=True).start()
    
    def uninstall_package(self, package_name: str, ui_callback=None):
        """Uninstall package from current environment."""
        if self._shutting_down.is_set():
            if ui_callback:
                ui_callback(False, "Shutting down")
            return
        
        def uninstall_task():
            if self.signals:
                self.signals.operation_started.emit("uninstall", package_name)
            
            try:
                pip_cmd = self.get_pip_command()
                cmd = ["uninstall", "-y", package_name]
                
                success, message, _ = run_pip_with_real_progress(
                    cmd, 
                    progress_callback=lambda d: self.signals.operation_progress.emit(d) if self.signals else None,
                    pip_cmd=pip_cmd
                )
                
                if not success:
                    raise Exception(message)
                
                # Remove from local list
                with self.lock:
                    self.packages = [p for p in self.packages if p["name"].lower() != package_name.lower()]
                    self._search_cache.clear()
                
                if not self._shutting_down.is_set():
                    if ui_callback:
                        ui_callback(True, f"Uninstalled {package_name}")
                    if self.signals:
                        self.signals.operation_completed.emit(True, f"Uninstalled {package_name}")
                    
            except Exception as e:
                logger.error(f"Uninstall failed: {e}")
                if not self._shutting_down.is_set():
                    if ui_callback:
                        ui_callback(False, str(e))
                    if self.signals:
                        self.signals.operation_completed.emit(False, str(e))
        
        threading.Thread(target=uninstall_task, daemon=True).start()

    def is_operation_active(self):
        """Check if any background operation is currently active."""
        if self.checking:
            return True
        with self.lock:
            if hasattr(self, '_active_futures') and self._active_futures:
                return True
        return False

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
                            
                            try:
                                from .utils import VersionComparator
                                status = "Updated" if not VersionComparator().is_outdated(installed_version, latest_version) else "Outdated"
                            except:
                                # Fallback with normalization
                                status = "Updated" if str(installed_version).strip().lower() == str(latest_version).strip().lower() else "Outdated"
                            
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
                
                try:
                    from .utils import VersionComparator
                    status = "Updated" if not VersionComparator().is_outdated(installed_version, latest_version) else "Outdated"
                except:
                    # Fallback with normalization
                    status = "Updated" if str(installed_version).strip().lower() == str(latest_version).strip().lower() else "Outdated"
                
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
    
    def load_packages(self, ui_callback, force_refresh: bool = False):
        """Load packages (legacy method)."""
        self.load_packages_with_cache(ui_callback, None, force_refresh=force_refresh)
    
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
    
    def search_packages(self, term, ui_callback=None):
        """Search local packages by name (Threaded with Cancellation)."""
        # Cancel previous search
        self._search_cancel.set()
        self._search_cancel = threading.Event()
        current_cancel = self._search_cancel
        
        def search_task():
            try:
                term_lower = term.lower()
                with self.lock:
                    if current_cancel.is_set(): return
                    results = [p for p in self.packages if term_lower in p["name"].lower()]
                
                if not current_cancel.is_set() and ui_callback:
                    ui_callback(results)
            except Exception as e:
                logger.error(f"Search failed: {e}")
        
        threading.Thread(target=search_task, daemon=True).start()
    
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

    def check_updates_with_signals(self, signals=None):
        """Legacy wrapper - now just uses internal check_updates"""
        self.check_updates()
    
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