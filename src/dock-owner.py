#!/usr/bin/env python3
"""Read and maintain the per-config identity of the managed Eww daemon."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import sys
import tempfile
import time


def canonical_config(config_dir=None):
    value = config_dir or os.environ.get("DOCK_CONFIG_DIR") or str(Path(__file__).resolve().parent)
    return str(Path(value).expanduser().resolve(strict=True))


def runtime_directory(runtime_dir=None, create=False):
    value = runtime_dir or os.environ.get("DOCK_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        value = str(Path.home() / ".cache" / "eww" / "runtime")
    path = Path(value).expanduser().resolve()
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("runtime directory must be owned by this user and mode 0700")
    return str(path)


def _paths(config_dir=None, runtime_dir=None, create=False):
    config = canonical_config(config_dir)
    runtime = runtime_directory(runtime_dir, create=create)
    namespace = hashlib.sha256((str(os.getuid()) + "\0" + config).encode()).hexdigest()[:24]
    base = Path(runtime) / ("eww-dock-" + namespace)
    if create:
        base.mkdir(mode=0o700, exist_ok=True)
    return config, runtime, base


def _proc_stat(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    tail = raw[raw.rfind(")") + 2 :].split()
    return int(tail[19])


def _proc_uid(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise ValueError("missing process uid")


def _proc_env(pid):
    values = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            values[key.decode(errors="replace")] = value.decode(errors="replace")
    return values


def _proc_args(pid):
    return [part.decode(errors="replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]


def _arg_config(args):
    for index, item in enumerate(args):
        if item == "--config" and index + 1 < len(args):
            return args[index + 1]
        if item.startswith("--config="):
            return item.split("=", 1)[1]
    return None


def _socket_listeners(pid):
    fds = Path(f"/proc/{pid}/fd")
    inodes = set()
    for fd in fds.iterdir():
        try:
            link = os.readlink(fd)
        except OSError:
            continue
        if link.startswith("socket:[") and link.endswith("]"):
            inodes.add(link[8:-1])
    found = []
    try:
        rows = Path("/proc/net/unix").read_text().splitlines()[1:]
    except OSError:
        return found
    for row in rows:
        fields = row.split(maxsplit=7)
        if len(fields) < 7 or fields[3] != "00010000" or fields[6] not in inodes:
            continue
        if len(fields) == 8 and fields[7].startswith("/"):
            found.append(fields[7])
    return sorted(set(found))


def _same_identity(pid, start_time, config, display):
    try:
        if _proc_stat(pid) != int(start_time) or _proc_uid(pid) != os.getuid():
            return False
        args = _proc_args(pid)
        if not args or Path(args[0]).name != "eww" or "daemon" not in args:
            return False
        proc_config = _arg_config(args)
        if not proc_config or str(Path(proc_config).resolve()) != config:
            return False
        if _proc_env(pid).get("DISPLAY", "") != display:
            return False
        return True
    except (OSError, ValueError, IndexError, PermissionError):
        return False


def _same_supervisor(metadata):
    try:
        pid = int(metadata["supervisor_pid"])
        args = _proc_args(pid)
        return (
            _proc_stat(pid) == int(metadata["supervisor_start_time"])
            and _proc_uid(pid) == os.getuid()
            and "run" in args
            and any(Path(arg).name == "dock-daemon.sh" for arg in args)
            and _proc_env(pid).get("DISPLAY", "") == metadata["display"]
        )
    except (OSError, ValueError, TypeError, KeyError, PermissionError):
        return False


def _supervisor_gone(metadata):
    """True only when the recorded supervisor is absent, exited, or pid-reused."""
    if _same_supervisor(metadata):
        return False
    try:
        pid = int(metadata["supervisor_pid"])
        start_time = int(metadata["supervisor_start_time"])
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return tail[0] in ("Z", "X") or int(tail[19]) != start_time
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


def _socket_peer_matches(path, pid):
    """Check the currently addressable listener, not an unlinked old socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(0.15)
            peer.connect(path)
            peer_pid, peer_uid, _ = struct.unpack(
                "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            )
            return peer_pid == pid and peer_uid == os.getuid()
    except (OSError, ValueError, struct.error):
        return False


def verified_owner(config_dir=None, runtime_dir=None, probe_socket=True):
    """Return verified managed daemon metadata, or None for any uncertainty.

    probe_socket=False skips the connecting peer check for restacking only;
    a connect without a message makes Eww log an IPC error on every call.
    """
    try:
        config, _, base = _paths(config_dir, runtime_dir)
        metadata = json.loads((base / "owner.json").read_text())
        pid = metadata["pid"]
        start_time = metadata["start_time"]
        display = metadata["display"]
        socket_path = metadata["socket_path"]
        if not isinstance(pid, int) or not isinstance(start_time, int) or not isinstance(display, str) or not display:
            return None
        if display != os.environ.get("DISPLAY", ""):
            return None
        if metadata.get("config_dir") != config or not _same_identity(pid, start_time, config, display):
            return None
        if not _same_supervisor(metadata):
            return None
        if socket_path not in _socket_listeners(pid) or (probe_socket and not _socket_peer_matches(socket_path, pid)):
            return None
        return {
            "pid": pid,
            "start_time": start_time,
            "config_dir": config,
            "display": display,
            "socket_path": socket_path,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".owner.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _unmanaged(config, display):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            args = _proc_args(pid)
            if not args or Path(args[0]).name != "eww" or _proc_uid(pid) != os.getuid():
                continue
            proc_config = _arg_config(args)
            if proc_config is None:
                proc_config = str(Path.home() / ".config" / "eww")
            if str(Path(proc_config).resolve()) != config or _proc_env(pid).get("DISPLAY", "") != display:
                continue
            if _socket_listeners(pid):
                return pid
        except (OSError, ValueError, PermissionError):
            continue
    return None


def _record(pid, config_dir=None, runtime_dir=None, timeout=3.0, supervisor_pid=None, supervisor_start_time=None):
    config, _, base = _paths(config_dir, runtime_dir, create=True)
    display = os.environ.get("DISPLAY", "")
    if not display:
        raise ValueError("DISPLAY is empty; refusing to claim an owner")
    expected = str(Path(config).resolve())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _proc_uid(pid) != os.getuid():
            raise ValueError("daemon uid does not match current uid")
        args = _proc_args(pid)
        if not args or Path(args[0]).name != "eww" or "daemon" not in args or str(Path(_arg_config(args) or "").resolve()) != expected:
            raise ValueError("child is not the requested explicit-config Eww daemon")
        if _proc_env(pid).get("DISPLAY", "") != display:
            raise ValueError("daemon display does not match supervisor display")
        sockets = _socket_listeners(pid)
        if sockets:
            if len(sockets) != 1:
                raise ValueError("daemon has an ambiguous set of IPC listening sockets")
            supervisor = supervisor_pid if supervisor_pid is not None else os.getppid()
            record = {
                "pid": int(pid),
                "start_time": _proc_stat(pid),
                "config_dir": config,
                "display": display,
                "socket_path": sockets[0],
                "supervisor_pid": supervisor,
                "supervisor_start_time": supervisor_start_time if supervisor_start_time is not None else _proc_stat(supervisor),
            }
            _atomic_json(base / "owner.json", record)
            if verified_owner(config, runtime_dir):
                return record
            raise ValueError("Eww listener failed owner identity verification")
        time.sleep(0.025)
    raise TimeoutError("Eww IPC listener did not become ready")


def _remove(config_dir=None, runtime_dir=None, supervisor_pid=None, supervisor_start_time=None):
    config, _, base = _paths(config_dir, runtime_dir)
    path = base / "owner.json"
    try:
        metadata = json.loads(path.read_text())
        if metadata.get("supervisor_pid") != supervisor_pid or metadata.get("supervisor_start_time") != supervisor_start_time:
            return False
        if _proc_stat(supervisor_pid) != supervisor_start_time:
            return False
        path.unlink()
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _stop_child(pid, timeout):
    """Only signal our supervisor's unreaped child; shutdown is bounded."""
    try:
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if int(tail[1]) != os.getppid() or _proc_uid(pid) != os.getuid():
            raise ValueError("refusing to stop a process that is not the supervisor's child")
        start = int(tail[19])
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if int(current[19]) != start or current[0] in ("Z", "X"):
                return
            time.sleep(0.025)
        if _proc_stat(pid) == start:
            os.kill(pid, signal.SIGKILL)
    except FileNotFoundError:
        return


def _reclaim(config_dir=None, runtime_dir=None, timeout=1.0):
    """Stop the recorded Eww after its supervisor died; caller holds the lifetime lock.

    Returns the reclaimed pid, or None on any uncertainty. The pidfd pins the
    process, so the signal cannot reach a pid reused after the identity check.
    """
    config, _, base = _paths(config_dir, runtime_dir)
    path = base / "owner.json"
    try:
        metadata = json.loads(path.read_text())
        pid, start_time, display = metadata["pid"], metadata["start_time"], metadata["display"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(pid, int) or not isinstance(start_time, int) or not isinstance(display, str) or not display:
        return None
    if display != os.environ.get("DISPLAY", "") or metadata.get("config_dir") != config:
        return None
    if not _same_identity(pid, start_time, config, display) or not _supervisor_gone(metadata):
        return None
    try:
        fd = os.pidfd_open(pid)
    except (OSError, AttributeError):
        return None
    try:
        if not _same_identity(pid, start_time, config, display):
            return None
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            # A pidfd becomes readable when its process exits, even before reaping.
            if not select.select([fd], [], [], timeout)[0]:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                if not select.select([fd], [], [], timeout)[0]:
                    raise TimeoutError("orphaned Eww daemon survived SIGKILL")
        except ProcessLookupError:
            pass  # It exited between the identity check and the signal.
    finally:
        os.close(fd)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return pid


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir")
    parser.add_argument("--runtime-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("paths")
    commands.add_parser("unmanaged")
    wait = commands.add_parser("wait")
    wait.add_argument("--timeout", type=float, default=3.0)
    stop = commands.add_parser("stop")
    stop.add_argument("pid", type=int)
    stop.add_argument("--timeout", type=float, default=1.0)
    record = commands.add_parser("record")
    record.add_argument("pid", type=int)
    record.add_argument("--timeout", type=float, default=3.0)
    record.add_argument("--supervisor-pid", type=int)
    record.add_argument("--supervisor-start-time", type=int)
    remove = commands.add_parser("remove")
    remove.add_argument("supervisor_pid", type=int)
    remove.add_argument("supervisor_start_time", type=int)
    reclaim = commands.add_parser("reclaim")
    reclaim.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()
    try:
        config, runtime, base = _paths(args.config_dir, args.runtime_dir, create=args.command == "paths")
        if args.command == "paths":
            print(json.dumps({"config_dir": config, "runtime_dir": runtime, "namespace_dir": str(base), "lock_path": str(base / "owner.lock")}))
        elif args.command == "unmanaged":
            pid = _unmanaged(config, os.environ.get("DISPLAY", ""))
            if pid is not None:
                print(f"unmanaged Eww daemon pid {pid} uses this config/display; controlled migration required", file=sys.stderr)
                return 1
            return 0
        elif args.command == "record":
            print(json.dumps(_record(args.pid, config, runtime, args.timeout,
                                     args.supervisor_pid, args.supervisor_start_time)))
        elif args.command == "wait":
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if verified_owner(config, runtime):
                    return 0
                time.sleep(0.025)
            raise TimeoutError("no verified managed Eww owner became ready")
        elif args.command == "remove":
            _remove(config, runtime, args.supervisor_pid, args.supervisor_start_time)
        elif args.command == "stop":
            _stop_child(args.pid, args.timeout)
        elif args.command == "reclaim":
            pid = _reclaim(config, runtime, args.timeout)
            if pid is not None:
                print(f"dock-owner: reclaimed orphaned Eww daemon pid {pid}", file=sys.stderr)
        return 0
    except (OSError, ValueError, TimeoutError) as error:
        print(f"dock-owner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
