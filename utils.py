"""📦 PyPI Manager - Utilities Module"""

import logging
import subprocess
import sys
import re
import os
import time
import threading
import ssl
import urllib.request
import urllib.error
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta


# Theme configuration
COLORS = {
    "bg": "#0a0a0a",
    "surface": "#121212",
    "card": "#1a1a1a",
    "accent": "#00bcd4",
    "accent_hover": "#0097a7",
    "text": "#ffffff",
    "subtext": "#888888",
    "success": "#4caf50",
    "warning": "#ff9800",
    "danger": "#f44336",
    "border": "#2a2a2a",
    "progress_bg": "#1e1e1e",
    "progress_fg": "#00bcd4"
}

# Production logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('PyScope')

# Security constants
MAX_PACKAGE_NAME_LENGTH = 100
MAX_INPUT_LENGTH = 100
MAX_URL_LENGTH = 2000
MAX_COMMAND_LENGTH = 10000
MAX_SEARCH_TERM_LENGTH = 100
MAX_JSON_SIZE = 30_000_000
MAX_REQUEST_ATTEMPTS = 3
REQUEST_RETRY_DELAY = 2


def validate_package_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate PyPI package names securely."""
    if not name or not isinstance(name, str):
        return False, "Package name must be a non-empty string"
    
    if len(name) > MAX_PACKAGE_NAME_LENGTH:
        return False, f"Package name too long (max {MAX_PACKAGE_NAME_LENGTH} characters)"
    
    # Check for unsafe characters
    if re.search(r'[{}[\]()*+?\\|^$]', name):
        return False, "Package name contains unsafe characters"
    
    # PyPI package name pattern
    try:
        pattern = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$')
        if not pattern.match(name):
            return False, f"Invalid package name format: {name[:50]}"
    except re.error as e:
        logger.error(f"Regex error: {e}")
        return False, "Internal validation error"
    
    return True, None


def validate_search_term(term: str) -> Tuple[bool, Optional[str]]:
    """Validate search terms securely."""
    if not term or not isinstance(term, str):
        return False, "Search term must be a non-empty string"
    
    if len(term) > MAX_SEARCH_TERM_LENGTH:
        return False, f"Search term too long (max {MAX_SEARCH_TERM_LENGTH} characters)"
    
    if re.search(r'[{}[\]()*+?\\|^$<>!@#$%^&*=]', term):
        return False, "Search term contains unsafe characters"
    
    return True, None


def sanitize_pip_args(args: List[str]) -> List[str]:
    """Sanitize pip arguments with injection prevention."""
    if not isinstance(args, list):
        raise ValueError("Arguments must be a list")
    
    sanitized = []
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            raise ValueError(f"Argument {i} must be a string")
        
        if len(arg) > 500:
            raise ValueError(f"Argument {i} too long (max 500 characters)")
        
        # Remove shell metacharacters
        arg = re.sub(r'[;&|`$<>(){}[\]*+?\\|^]', '', arg)
        
        # Validate package names
        if arg and not arg.startswith('-'):
            base_name = arg.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0]
            base_name = base_name.split('>')[0].split('<')[0].split('~=')[0]
            
            is_valid, error_msg = validate_package_name(base_name)
            if not is_valid:
                raise ValueError(f"Invalid package argument '{arg}': {error_msg}")
        
        sanitized.append(arg)
    
    return sanitized


def safe_regex_search(pattern: str, text: str) -> Optional[re.Match]:
    """Safe regex search with size limits."""
    if not text or len(text) > 10000:
        logger.warning(f"Text too long for regex search: {len(text)} chars")
        return None
    
    try:
        compiled = re.compile(pattern, re.DOTALL)
        return compiled.search(text)
    except re.error as e:
        logger.error(f"Regex error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected regex error: {e}")
        return None


def run_pip_safe(args: List[str], timeout: int = 300, pip_cmd: Optional[List[str]] = None) -> str:
    """Safely execute pip commands with validation."""
    if not args:
        raise ValueError("No arguments provided")
    
    # Validate total length
    total_length = sum(len(arg) for arg in args)
    if total_length > MAX_COMMAND_LENGTH:
        raise ValueError(f"Command too long (max {MAX_COMMAND_LENGTH} characters)")
    
    try:
        sanitized_args = sanitize_pip_args(args)
        safe_log = ' '.join(sanitized_args[:3]) + (' ...' if len(sanitized_args) > 3 else '')
        
        # Use provided pip command or default
        if pip_cmd:
            if not isinstance(pip_cmd, list):
                raise ValueError("pip_cmd must be a list")
            
            # Validate pip command
            for i, cmd_part in enumerate(pip_cmd):
                if not isinstance(cmd_part, str):
                    raise ValueError(f"pip_cmd[{i}] must be a string")
                if len(cmd_part) > 500:
                    raise ValueError(f"pip_cmd[{i}] too long (max 500 characters)")
            
            cmd = pip_cmd + sanitized_args
            logger.info(f"Executing pip (custom): {' '.join(pip_cmd[:3])} {safe_log}")
        else:
            cmd = [sys.executable, "-m", "pip"] + sanitized_args
            logger.info(f"Executing pip (system): {safe_log}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            encoding='utf-8',
            errors='replace'
        )
        
        if result.returncode != 0:
            error_msg = result.stderr[:500] if result.stderr else "Unknown error"
            logger.error(f"Pip failed (code {result.returncode}): {error_msg}")
            
            # User-friendly errors
            if "PermissionError" in error_msg or "permission denied" in error_msg.lower():
                raise Exception("Permission denied - try admin privileges")
            elif "Network is unreachable" in error_msg or "connection" in error_msg.lower():
                raise Exception("Network error - check internet connection")
            elif "Could not find a version" in error_msg:
                raise Exception(f"Package version not found: {error_msg[:100]}")
            else:
                raise Exception(f"Command failed: {error_msg[:200]}")
        
        if result.stdout:
            logger.debug(f"Pip output: {result.stdout[:200]}...")
        
        return result.stdout
        
    except subprocess.TimeoutExpired:
        logger.error(f"Pip timeout after {timeout}s")
        raise Exception(f"Operation timed out after {timeout} seconds")
    except FileNotFoundError:
        logger.error("Python/pip not found")
        raise Exception("Python/pip not found - ensure Python is installed")
    except Exception as e:
        logger.exception(f"Unexpected pip error")
        raise


def run_pip_with_real_progress(args: List[str], progress_callback=None, 
                              timeout: int = 300, pip_cmd: Optional[List[str]] = None):
    """Run pip with real-time progress tracking."""
    if not args:
        raise ValueError("No arguments provided")
    
    # Validate length
    total_length = sum(len(arg) for arg in args)
    if total_length > MAX_COMMAND_LENGTH:
        raise ValueError(f"Command too long (max {MAX_COMMAND_LENGTH} characters)")
    
    try:
        sanitized_args = sanitize_pip_args(args)
        safe_log = ' '.join(sanitized_args[:3]) + (' ...' if len(sanitized_args) > 3 else '')
        
        # Use provided pip command or default
        if pip_cmd:
            if not isinstance(pip_cmd, list):
                raise ValueError("pip_cmd must be a list")
            
            # Validate pip command
            for i, cmd_part in enumerate(pip_cmd):
                if not isinstance(cmd_part, str):
                    raise ValueError(f"pip_cmd[{i}] must be a string")
                if len(cmd_part) > 500:
                    raise ValueError(f"pip_cmd[{i}] too long (max 500 characters)")
            
            cmd = pip_cmd + sanitized_args
            logger.info(f"Executing pip with progress (custom): {' '.join(pip_cmd[:3])} {safe_log}")
        else:
            cmd = [sys.executable, "-m", "pip"] + sanitized_args
            logger.info(f"Executing pip with progress (system): {safe_log}")
        
        # Send start notification
        if progress_callback:
            progress_callback({
                'type': 'start',
                'command': safe_log,
                'timestamp': time.time(),
                'pip_command': 'custom' if pip_cmd else 'system'
            })
        
        # Start process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace'
        )
        
        all_output = []
        cancelled = threading.Event()
        
        def read_stream(stream, is_stderr=False):
            """Read stream in real-time."""
            try:
                for line in iter(stream.readline, ''):
                    if cancelled.is_set():
                        break
                    
                    line = line.rstrip()
                    if line:
                        all_output.append(line)
                        
                        # Parse pip output
                        parsed_data = parse_pip_output(line)
                        
                        if parsed_data:
                            if progress_callback:
                                progress_callback(parsed_data)
                        else:
                            # Send raw output
                            if progress_callback:
                                progress_callback({
                                    'type': 'output',
                                    'line': line,
                                    'is_stderr': is_stderr
                                })
            except Exception as e:
                logger.error(f"Stream read error: {e}")
        
        # Start reader threads
        stdout_thread = threading.Thread(
            target=read_stream, args=(process.stdout, False), daemon=True
        )
        stderr_thread = threading.Thread(
            target=read_stream, args=(process.stderr, True), daemon=True
        )
        
        stdout_thread.start()
        stderr_thread.start()
        
        # Wait for completion
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            cancelled.set()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            
            error_msg = f"Operation timed out after {timeout} seconds"
            logger.error(f"Pip timeout: {error_msg}")
            
            if progress_callback:
                progress_callback({'type': 'error', 'message': error_msg})
            
            return False, error_msg, all_output
        
        # Wait for reader threads
        cancelled.set()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        
        # Check exit code
        if return_code != 0:
            error_msg = '\n'.join(all_output[-10:]) if all_output else "Unknown error"
            logger.error(f"Pip failed (code {return_code}): {error_msg[:200]}")
            
            if progress_callback:
                progress_callback({
                    'type': 'error',
                    'message': f"Command failed with code {return_code}",
                    'details': error_msg[:500]
                })
            
            # User-friendly errors
            if "PermissionError" in error_msg or "permission denied" in error_msg.lower():
                return False, "Permission denied - try admin privileges", all_output
            elif "Network is unreachable" in error_msg or "connection" in error_msg.lower():
                return False, "Network error - check internet connection", all_output
            elif "Could not find a version" in error_msg:
                return False, "Package version not found", all_output
            else:
                return False, f"Installation failed: {error_msg[:200]}", all_output
        
        # Send success
        if progress_callback:
            progress_callback({
                'type': 'success',
                'message': "Command completed successfully",
                'output_lines': len(all_output)
            })
        
        logger.info(f"Pip completed in {timeout}s")
        return True, "Successfully completed", all_output
        
    except FileNotFoundError:
        error_msg = "Python or pip not found in PATH"
        logger.error(error_msg)
        if progress_callback:
            progress_callback({'type': 'error', 'message': error_msg})
        return False, error_msg, []
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception(f"Unexpected pip error")
        if progress_callback:
            progress_callback({'type': 'error', 'message': error_msg})
        return False, error_msg, []


def parse_pip_output(line: str) -> Optional[Dict[str, Any]]:
    """Parse pip output for progress information."""
    try:
        # Collecting package
        match = re.search(r'Collecting\s+([^\s]+)', line)
        if match:
            return {'type': 'collecting', 'package': match.group(1), 'line': line}
        
        # Downloading with size
        match = re.search(r'Downloading\s+[^\s]+\s+\(([\d.]+)\s*([KMGT]?B)\)', line)
        if match:
            size = float(match.group(1))
            unit = match.group(2).upper()
            size_mb = convert_to_mb(size, unit)
            return {'type': 'download_start', 'size': size_mb, 'size_str': f"{size} {unit}", 'line': line}
        
        # Progress with percentage
        match = re.search(r'(\d+)%\s+\|\s+([\d.]+)\s*([KMGT]?B)\s+\|\s+([\d.]+)\s*([KMGT]?B)/s\s+\|\s+([\d:]+)', line)
        if match:
            percentage = int(match.group(1))
            downloaded = float(match.group(2))
            downloaded_unit = match.group(3).upper()
            speed = float(match.group(4))
            speed_unit = match.group(5).upper()
            eta = match.group(6)
            
            downloaded_mb = convert_to_mb(downloaded, downloaded_unit)
            speed_mbps = convert_to_mb(speed, speed_unit)
            
            return {
                'type': 'progress',
                'percentage': percentage,
                'downloaded': downloaded_mb,
                'speed': speed_mbps,
                'eta': eta,
                'line': line
            }
        
        # Other events
        if 'Installing collected packages' in line:
            return {'type': 'installing', 'line': line}
        
        match = re.search(r'Successfully\s+installed\s+([^\s]+)', line)
        if match:
            return {'type': 'success', 'package': match.group(1), 'line': line}
        
        if 'Requirement already satisfied' in line:
            return {'type': 'already_satisfied', 'line': line}
        
        if 'Building wheels' in line:
            return {'type': 'building', 'line': line}
        
        if 'Running setup.py' in line:
            return {'type': 'running_setup', 'line': line}
        
        return None
        
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        return None


def convert_to_mb(value: float, unit: str) -> float:
    """Convert size to megabytes."""
    unit = unit.upper()
    if unit == 'KB':
        return value / 1024
    elif unit == 'MB':
        return value
    elif unit == 'GB':
        return value * 1024
    elif unit == 'TB':
        return value * 1024 * 1024
    else:  # Assume bytes
        return value / (1024 * 1024)


def create_secure_ssl_context() -> ssl.SSLContext:
    """Create SSL context with modern security."""
    try:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20')
        return context
    except Exception as e:
        logger.warning(f"SSL context error: {e}")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context


def safe_urlopen(url: str, timeout: int = 10, headers=None, max_attempts: int = MAX_REQUEST_ATTEMPTS):
    """Safely open URL with retry logic and error handling."""
    if not url or not isinstance(url, str):
        logger.error(f"Invalid URL: {url}")
        return None
    
    if len(url) > MAX_URL_LENGTH:
        logger.error(f"URL too long: {len(url)} characters")
        return None
    
    for attempt in range(max_attempts):
        try:
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or parsed.scheme not in ['http', 'https']:
                logger.error(f"Invalid URL scheme: {url}")
                return None
            
            context = create_secure_ssl_context()
            
            req_headers = {
                'User-Agent': 'PyScope',
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip, deflate'
            }
            
            if headers:
                req_headers.update(headers)
            
            req = urllib.request.Request(url, headers=req_headers)
            return urllib.request.urlopen(req, timeout=timeout, context=context)
                    
        except urllib.error.HTTPError as e:
            if attempt < max_attempts - 1:
                time.sleep(REQUEST_RETRY_DELAY)
                continue
            logger.error(f"HTTP error: {e}")
            return None
        except Exception as e:
            if attempt < max_attempts - 1:
                time.sleep(REQUEST_RETRY_DELAY)
                continue
            logger.error(f"URL open error: {e}")
            return None
    
    return None


def validate_input_length(text: str, max_length: int = MAX_INPUT_LENGTH):
    """Validate input length."""
    if not text:
        return True, None
    
    if len(text) > max_length:
        return False, f"Input too long (max {max_length} characters)"
    
    return True, None


def safe_string_truncate(text: str, max_length: int) -> str:
    """Safely truncate string to specified length."""
    if not text:
        return text
    
    if len(text) > max_length:
        logger.warning(f"Truncating string from {len(text)} to {max_length}")
        return text[:max_length]
    
    return text


def get_python_version(python_path: str) -> str:
    """Get Python version from executable."""
    try:
        result = subprocess.run(
            [python_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False
        )
        
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return "Unknown"
    except Exception as e:
        logger.error(f"Failed to get Python version for {python_path}: {e}")
        return "Unknown"


def get_pip_version(pip_cmd: List[str]) -> str:
    """Get pip version from command."""
    try:
        result = subprocess.run(
            pip_cmd + ["--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False
        )
        
        if result.returncode == 0:
            match = re.search(r'pip\s+([\d.]+)', result.stdout)
            if match:
                return f"pip {match.group(1)}"
            return result.stdout.split()[1] if len(result.stdout.split()) > 1 else "Unknown"
        else:
            return "Unknown"
    except Exception as e:
        logger.error(f"Failed to get pip version: {e}")
        return "Unknown"


def validate_python_executable(python_path: str) -> bool:
    """Validate if a Python executable is valid."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import sys; print(sys.version[:5])"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False
        )
        
        return result.returncode == 0 and result.stdout.strip()
    except Exception:
        return False


def get_environment_info(pip_cmd: Optional[List[str]] = None) -> Dict[str, Any]:
    """Get information about current Python environment."""
    try:
        # Determine Python path
        python_path = sys.executable
        if pip_cmd and len(pip_cmd) > 0:
            if pip_cmd[0].endswith('python') or pip_cmd[0].endswith('python.exe'):
                python_path = pip_cmd[0]
        
        # Get Python version
        python_version = get_python_version(python_path)
        
        # Get pip version
        pip_version_cmd = pip_cmd or [sys.executable, "-m", "pip"]
        pip_version = get_pip_version(pip_version_cmd)
        
        # Get environment type
        env_type = "system"
        if hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix:
            env_type = "venv"
        elif os.environ.get('CONDA_PREFIX'):
            env_type = "conda"
        elif os.environ.get('VIRTUAL_ENV'):
            env_type = "virtualenv"
        
        return {
            "python_path": python_path,
            "python_version": python_version,
            "pip_version": pip_version,
            "environment_type": env_type,
            "pip_command": pip_cmd or [sys.executable, "-m", "pip"]
        }
    except Exception as e:
        logger.error(f"Failed to get environment info: {e}")
        return {
            "python_path": sys.executable,
            "python_version": "Unknown",
            "pip_version": "Unknown",
            "environment_type": "unknown",
            "pip_command": [sys.executable, "-m", "pip"]
        }


def check_pip_available(pip_cmd: Optional[List[str]] = None) -> bool:
    """Check if pip is available in the current environment."""
    try:
        cmd = pip_cmd or [sys.executable, "-m", "pip"]
        result = subprocess.run(
            cmd + ["--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False
        )
        
        return result.returncode == 0
    except Exception:
        return False


def get_python_environment_list() -> List[Dict[str, Any]]:
    """Get list of available Python environments."""
    environments = []
    
    # Add current system environment
    current_env = get_environment_info()
    current_env["name"] = "System Python"
    current_env["display"] = f"🐍 System Python ({current_env['python_version']})"
    environments.append(current_env)
    
    return environments