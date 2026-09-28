"""Native SecureCRT adapter. Standard library only; all crt calls on script thread."""

import json
import os
import queue
import re
import runpy
import socketserver
import stat
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

MAX_OUTPUT = 1_000_000
MAX_REQUEST = 64_000
MAX_JOBS = 10
# ReadString returns an empty string on timeout, even after receiving data.
# Match each ASCII character so unfinished CLI lines are captured immediately.
# Non-ASCII text preceding a match is returned by ReadString too.
CHARACTERS = [chr(i) for i in range(1, 128)]


def runtime_directory():
    # Short path: macOS Unix socket paths are limited to approximately 104 bytes.
    path = Path("/tmp") / ("securecrt-mcp-%s" % os.getuid())
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Unsafe SecureCRT runtime directory: %s" % path)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("SecureCRT runtime directory must have permissions 0700")
    return path


def validate_command(command):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Command must be a non-empty string")
    if len(command) > 4096 or not command.isprintable():
        raise ValueError("Use one printable command line, at most 4096 characters")
    return command


# SecureCRT retains its interpreter and may not put this folder on sys.path.
# Load by absolute path on every bridge start, avoiding stale imported profiles.
_profiles = runpy.run_path(str(Path(__file__).with_name("command_profiles.py")))
COMMAND_PROFILES = _profiles["COMMAND_PROFILES"]
available_command_profiles = _profiles["available_command_profiles"]
device_shell_for_profile = _profiles["device_shell_for_profile"]


def _profile_file(session_id):
    return runtime_directory() / (session_id + ".profile")


def get_command_profile(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("Invalid session ID")
    path = _profile_file(session_id)
    if not path.exists():
        return None
    try:
        profile = path.read_text().strip()
    except OSError as exc:
        raise ValueError("Cannot read command profile; commands are blocked") from exc
    if profile not in COMMAND_PROFILES:
        raise ValueError("Unknown command profile; commands are blocked")
    return profile


def set_command_profile(session_id, profile):
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("Invalid session ID")
    if profile not in COMMAND_PROFILES:
        raise ValueError("Choose one of: %s" % ", ".join(COMMAND_PROFILES))
    path = _profile_file(session_id)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(profile + "\n")
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    return profile


def authorize_command(session_id, command):
    validate_command(command)
    profile = get_command_profile(session_id)
    if profile is None:
        raise ValueError("No command profile selected; restart securecrt_bridge.py and choose one")
    prefixes = COMMAND_PROFILES[profile]["commands"]
    if prefixes is None:
        return
    tokens = command.strip().split()
    safe_token = re.compile(r"[A-Za-z0-9_.:/-]+")
    for token in tokens:
        if not safe_token.fullmatch(token):
            raise ValueError("Read-only profiles allow configured command prefixes with simple arguments only")

    # Compare complete words: "show" is allowed, but "showcase" is not.
    words = command.lower().split()
    for prefix in prefixes:
        prefix_words = prefix.split()
        if words[:len(prefix_words)] == prefix_words:
            return

    allowed = ", ".join(prefixes)
    raise ValueError("Command blocked by %s profile (allowed prefixes: %s)" % (profile, allowed))


def authorize_input(session_id, text, press_enter):
    profile = get_command_profile(session_id)
    if profile is None:
        raise ValueError("No command profile selected; interactive input is blocked")
    if COMMAND_PROFILES[profile]["commands"] is None:
        return
    if text != " " or press_enter:
        raise ValueError("Read-only profiles allow pager advance only (one space, no Enter)")


def validate_prompt(prompt):
    if not isinstance(prompt, str) or not 2 <= len(prompt.strip()) <= 240:
        raise ValueError("Use the full prompt (2–240 characters), not just # or >")
    if not prompt.isprintable():
        raise ValueError("Prompt must be printable text on one line")
    return prompt.rstrip()


class SessionEngine:
    """One fixed Tab object; switching/reordering the GUI cannot retarget commands."""

    def __init__(self, tab, session_id, version, clock=time.monotonic):
        self.tab = tab
        self.screen = tab.Screen
        self.session_id = session_id
        self.version = version
        self.clock = clock
        self.prompt = None
        self.jobs = OrderedDict()
        self.active = None
        self.stopping = False
        self.original = (self.screen.Synchronous, self.screen.IgnoreEscape)
        self.screen.Synchronous = False
        self.screen.IgnoreEscape = True

    def status(self):
        connected = bool(self.tab.Session.Connected)
        profile = get_command_profile(self.session_id)
        shell_source = None
        allowed_commands = None
        if profile is not None:
            shell_source = "user_selected_profile"
            allowed_commands = COMMAND_PROFILES[profile]["commands"]
        return {
            "session_id": self.session_id,
            "caption": self.tab.Caption,
            "saved_session": self.tab.Session.Path,
            "connected": connected,
            "command_profile": profile,
            "device_shell": device_shell_for_profile(profile),
            "device_shell_source": shell_source,
            "allowed_command_prefixes": allowed_commands,
            "securecrt_version": self.version,
            "prompt": self.prompt,
            "active_job_id": self.active,
            "recent_job_ids": list(self.jobs),
            "scope": "The existing tab in which securecrt_bridge.py was started",
        }

    def _start(self, request):
        job_id = request["job_id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job ID")
        if job_id in self.jobs:
            return self.output(job_id)
        if self.active:
            raise ValueError("Tab busy; read or interrupt active job %s" % self.active)
        if not self.tab.Session.Connected:
            raise ValueError("Tab disconnected; restart bridge after reconnecting")
        kind = request["operation"]
        timeout = request.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
            raise ValueError("Timeout must be between 1 and 300 seconds")
        if kind == "probe" and get_command_profile(self.session_id) is None:
            raise ValueError("No command profile selected; restart securecrt_bridge.py and choose one")
        command = ""
        if kind == "run":
            command = validate_command(request["command"])
            authorize_command(self.session_id, command)
        prompt = request.get("expected_prompt") or self.prompt
        if kind != "probe":
            if prompt is None:
                raise ValueError("Probe the idle prompt first, or supply expected_prompt")
            prompt = validate_prompt(prompt)
        while len(self.jobs) >= MAX_JOBS:
            self.jobs.popitem(last=False)
        self.jobs[job_id] = {
            "job_id": job_id, "session_id": self.session_id,
            "kind": kind, "command": command, "status": "running",
            "output": "", "truncated": False, "line": "", "prompt": prompt,
            "started": self.clock(), "deadline": self.clock() + timeout,
            "last_data": self.clock(), "interrupted": False,
            "completion": None,
        }
        self.active = job_id
        # Enable buffering BEFORE sending; idle output isn't mistaken for a reply.
        self.screen.Synchronous = True
        try:
            self.screen.Send(command + "\r")
        except Exception:
            self.jobs[job_id]["status"] = "send_failed_delivery_unknown"
            # Keep ownership: Send may have partially succeeded. Never retry it.
            raise
        return self.output(job_id)

    def output(self, job_id, cursor=0, limit=32_000):
        if job_id not in self.jobs:
            raise ValueError("Unknown/expired job; only the last %s jobs are kept" % MAX_JOBS)
        job = self.jobs[job_id]
        data = job["output"]
        if not isinstance(cursor, int) or not 0 <= cursor <= len(data):
            raise ValueError("Cursor must be within the captured output")
        if not isinstance(limit, int) or not 1 <= limit <= 64_000:
            raise ValueError("Limit must be between 1 and 64000 characters")
        end = min(len(data), cursor + limit)
        return {
            "session_id": self.session_id, "job_id": job_id,
            "status": job["status"], "command": job["command"],
            "output": data[cursor:end], "next_cursor": end,
            "has_more": end < len(data), "truncated": job["truncated"],
            "active": self.active == job_id, "prompt": job["prompt"],
            "completion": job["completion"],
            "elapsed_seconds": round(job.get("finished", self.clock()) - job["started"], 2),
            "error": job.get("error"),
        }

    def _finish(self, status, completion):
        job = self.jobs[self.active]
        job["status"] = status
        job["completion"] = completion
        job["finished"] = self.clock()
        self.active = None
        self.screen.Synchronous = False

    def _append(self, value):
        job = self.jobs[self.active]
        remaining = MAX_OUTPUT - len(job["output"])
        job["output"] += value[:remaining]
        if len(value) > remaining:
            job["truncated"] = True
        job["last_data"] = self.clock()
        for char in value:
            if char in "\r\n":
                job["line"] = ""
            elif char in "\b\x7f":
                job["line"] = job["line"][:-1]
            elif char.isprintable():
                job["line"] = (job["line"] + char)[-1024:]

    def check_completion(self, job, text, index):
        """Recognize the prompt, or mark a timeout without releasing the tab."""
        # Wait for stream idle: a prompt-shaped prefix may have more text coming.
        idle = not text and not index and self.clock() - job["last_data"] >= 1
        if job["kind"] != "probe" and idle and job["line"].rstrip() == job["prompt"]:
            self.prompt = job["prompt"]
            status = "completed"
            if job["interrupted"]:
                status = "interrupted"
            self._finish(status, "prompt_matched")
        elif job["kind"] == "probe" and idle:
            candidate = job["line"].rstrip()
            if re.fullmatch(r"[^\r\n]{1,239}[#$>%]", candidate):
                self.prompt = candidate
                job["prompt"] = candidate
                self._finish("completed", "idle_prompt_detected")
            else:
                self._finish("prompt_not_detected", "provide_expected_prompt")
        elif job["status"] == "running" and self.clock() >= job["deadline"]:
            # Timeout does NOT mean the remote command stopped. Keep capturing
            # and keep the tab busy until its prompt returns or it is interrupted.
            job["status"] = "timed_out"

    def pump(self):
        if not self.active:
            return
        job = self.jobs[self.active]
        try:
            if not self.tab.Session.Connected:
                self._finish("disconnected", "connection_closed")
                self.stopping = True
                return
            text = self.screen.ReadString(CHARACTERS, 1)
            index = self.screen.MatchIndex
            if index:
                text += CHARACTERS[index - 1]
            if text:
                self._append(text)
            if not self.active:
                return
            self.check_completion(job, text, index)
        except Exception as exc:
            job["error"] = str(exc)
            self._finish("failed", "native_api_error")
            self.stopping = True

    def interrupt(self, request):
        """Send Ctrl+C only to the job that currently owns the tab."""
        if not self.active or self.active != request["job_id"]:
            raise ValueError("That job is not active; no Ctrl+C was sent")
        job = self.jobs[self.active]
        self.screen.Send("\x03")
        job["interrupted"] = True
        job["status"] = "interrupt_requested"
        return self.output(self.active)

    def send_input(self, request):
        """Send an explicit pager key or answer to the current job."""
        if not self.active or self.active != request["job_id"]:
            raise ValueError("That job is not active; no input was sent")
        text = request.get("text", "")
        if not isinstance(text, str) or len(text) > 4096 or (text and not text.isprintable()):
            raise ValueError("Input must be printable, at most 4096 characters")
        enter = request.get("press_enter", False)
        if not isinstance(enter, bool):
            raise ValueError("press_enter must be a boolean")
        authorize_input(self.session_id, text, enter)
        # Explicit input handles pagination/interactive prompts without guessing.
        if enter:
            text += "\r"
        self.screen.Send(text)
        return self.output(self.active)

    def dispatch(self, request):
        operation = request.get("operation")
        if operation == "status":
            return self.status()
        if operation in ("run", "probe"):
            return self._start(request)
        if operation == "output":
            return self.output(request["job_id"], request.get("cursor", 0), request.get("limit", 32_000))
        if operation == "interrupt":
            return self.interrupt(request)
        if operation == "input":
            return self.send_input(request)
        if operation == "detach":
            self.stopping = True
            return {"detached": True, "connection_closed": False,
                    "remote_command_may_still_be_running": self.active is not None}
        raise ValueError("Unknown bridge operation")

    def restore(self):
        self.screen.Synchronous, self.screen.IgnoreEscape = self.original


class BridgeRequestHandler(socketserver.StreamRequestHandler):
    """Socket threads exchange JSON only; the queue passes work to SecureCRT."""

    def handle(self):
        self.connection.settimeout(5)
        try:
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                raise ValueError("Invalid or oversized request")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("Expected JSON object")
            event = threading.Event()
            response = {}
            self.server.requests.put_nowait((request, event, response))
            if not event.wait(6):
                raise TimeoutError("SecureCRT script did not service request")
        except Exception as exc:
            response = {"error": str(exc)}
        try:
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
        except OSError:
            pass  # Delivery can fail after a command started; jobs remain queryable.


class BridgeServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False


def service_requests(engine, requests):
    """Handle queued requests on the SecureCRT script thread, never a socket thread."""
    for _ in range(32):
        try:
            request, event, response = requests.get_nowait()
        except queue.Empty:
            break
        try:
            if request.get("expires_at", 0) < time.time():
                raise ValueError("Request expired before execution; nothing sent")
            response["result"] = engine.dispatch(request)
        except Exception as exc:
            response["error"] = str(exc)
        finally:
            event.set()
        if engine.stopping:
            break


def run_bridge(crt, profile):
    tab = crt.GetScriptTab()
    if not tab.Session.Connected:
        crt.Dialog.MessageBox("Connect this tab first, then run securecrt_bridge.py.")
        return
    # 1. Create a private identity and remember the user-selected profile.
    session_id = uuid.uuid4().hex
    set_command_profile(session_id, profile)
    profile_path = _profile_file(session_id)
    path = runtime_directory() / (session_id + ".sock")
    requests = queue.Queue(maxsize=32)
    engine = SessionEngine(tab, session_id, str(crt.Version))

    server = None
    try:
        # 2. Start the local socket listener. It cannot call the SecureCRT API.
        server = BridgeServer(str(path), BridgeRequestHandler)
        server.requests = requests
        os.chmod(str(path), 0o600)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        # 3. Handle requests and read terminal output on this script thread.
        while not engine.stopping:
            service_requests(engine, requests)
            # Bound continuous-output processing so RPC requests stay responsive.
            until = time.monotonic() + 0.05
            while engine.active and not engine.stopping and time.monotonic() < until:
                engine.pump()
            if not tab.Session.Connected:
                break
            if engine.active:
                crt.Sleep(10)
            else:
                crt.Sleep(30)
    finally:
        # 4. Restore the tab settings and remove this bridge's runtime files.
        try:
            engine.restore()
        finally:
            if server:
                server.shutdown()
                server.server_close()
            path.unlink(missing_ok=True)
            profile_path.unlink(missing_ok=True)
