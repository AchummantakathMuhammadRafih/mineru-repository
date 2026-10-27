#!/usr/bin/env python
"""
Top-level entrypoint to run the MinerU Document Processing Server.
Supports both interactive foreground execution and background detached daemon mode.
"""

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PID_FILE = PROJECT_ROOT / "server.pid"
DEFAULT_LOG_FILE = PROJECT_ROOT / "server.log"

# Find log-file argument from argv if provided
log_file_arg = None
for i, arg in enumerate(sys.argv):
    if arg == "--log-file" and i + 1 < len(sys.argv):
        log_file_arg = sys.argv[i + 1]
        break

target_log_path = Path(log_file_arg).resolve() if log_file_arg else DEFAULT_LOG_FILE

# Guard against NoneType sys.stdout / sys.stderr in Windows detached mode
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    try:
        target_log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(target_log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        sys.stdout = open(os.devnull, "w")

if sys.stderr is None or not hasattr(sys.stderr, "write"):
    try:
        target_log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(target_log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        sys.stderr = open(os.devnull, "w")

# Force UTF-8 encoding on standard streams if available
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import signal
import subprocess
import time

from mineru_converter.core.patches import apply_transformers_compat_patches
apply_transformers_compat_patches()


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is currently active."""
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass

    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def get_running_server_pid() -> int:
    """Read PID file and return PID if active, else 0."""
    if not PID_FILE.exists():
        return 0
    try:
        pid = int(PID_FILE.read_text().strip())
        if is_process_running(pid):
            return pid
        # Stale PID file
        PID_FILE.unlink(missing_ok=True)
        return 0
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return 0


def stop_server() -> bool:
    """Stop any currently running server process."""
    pid = get_running_server_pid()
    if not pid:
        print("[i] No active MinerU server is currently running.")
        return False

    print(f"[*] Stopping MinerU server (PID {pid})...")
    try:
        import psutil
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            p.kill()
    except Exception:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    time.sleep(1.0)
    PID_FILE.unlink(missing_ok=True)
    print(f"[OK] MinerU server (PID {pid}) stopped successfully.")
    return True


def show_status() -> None:
    """Check and display running server status."""
    pid = get_running_server_pid()
    if pid:
        print(f"[OK] MinerU server is RUNNING in detached mode (PID: {pid}).")
        print(f"  * Log File:   {DEFAULT_LOG_FILE.resolve()}")
        print("  * Web UI:     http://localhost:8000/")
        print("  * API Docs:   http://localhost:8000/docs")
        print("  * To stop:    python server.py --stop")
    else:
        print("[i] MinerU server is NOT running.")


def run_detached(args, log_file: Path) -> None:
    """Spawn the server in detached background process."""
    existing_pid = get_running_server_pid()
    if existing_pid:
        print(f"[!] Server is already running in background (PID: {existing_pid}).")
        print("Use 'python server.py --stop' to terminate it first.")
        sys.exit(1)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_file, "a", encoding="utf-8")

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--host", args.host,
        "--port", str(args.port),
        "--log-file", str(log_file),
    ]
    if args.reload:
        cmd.append("--reload")

    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=log_fp,
            creationflags=flags,
            cwd=str(PROJECT_ROOT),
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
            cwd=str(PROJECT_ROOT),
        )

    PID_FILE.write_text(str(proc.pid))
    time.sleep(1.0)
    print("[OK] MinerU Conversion Server started in detached mode!")
    print(f"  * Process ID: {proc.pid}")
    print(f"  * Log File:   {log_file.resolve()}")
    print(f"  * Web UI:     http://localhost:{args.port}/")
    print(f"  * API Docs:   http://localhost:{args.port}/docs")
    print(f"  * View Logs:  Get-Content -Wait {log_file.name} (PowerShell) or tail -f {log_file.name}")
    print("  * To stop:    python server.py --stop")


def run_foreground(args, log_file: Path) -> None:
    """Run server in foreground mode."""
    import uvicorn
    from mineru_converter.utils.logging_utils import add_file_handler_to_loggers

    # Register PID
    PID_FILE.write_text(str(os.getpid()))

    # If running without a terminal, redirect stdout/stderr to log file
    if sys.stdout and not sys.stdout.isatty():
        try:
            log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
            sys.stdout = log_fp
            sys.stderr = log_fp
        except Exception:
            pass

    # Attach file handler to application and uvicorn loggers
    add_file_handler_to_loggers(log_file)

    print(f"[*] Starting MinerU Conversion Server on http://{args.host}:{args.port}")
    print(f"  * Web UI:   http://localhost:{args.port}/")
    print(f"  * API Docs: http://localhost:{args.port}/docs")
    print(f"  * Logging:  {log_file.resolve()}")
    print("  * Worker:   Async Sequential FIFO Queue (MinerU -> Hierarchy Correction -> Presidio Redaction)")

    try:
        uvicorn.run(
            "mineru_converter.server.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            access_log=False,
        )
    finally:
        PID_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="MinerU PDF Parser Async Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help=f"Path to log file (default: {DEFAULT_LOG_FILE.name})")
    parser.add_argument("-d", "--detach", action="store_true", help="Run server in background detached mode")
    parser.add_argument("--stop", action="store_true", help="Stop currently running server")
    parser.add_argument("--status", action="store_true", help="Check server process status")

    args = parser.parse_args()

    if args.stop:
        stop_server()
        return

    if args.status:
        show_status()
        return

    log_file = Path(args.log_file).resolve()
    if args.detach:
        run_detached(args, log_file)
    else:
        run_foreground(args, log_file)


if __name__ == "__main__":
    main()
