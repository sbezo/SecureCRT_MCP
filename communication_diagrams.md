# SecureCRT MCP communication diagrams

These diagrams describe the current Python implementation. They cover every production function and class; tests and the standalone toy example are shown separately. Arrows in flowcharts indicate calls or explicitly labeled data transfers. Names without a module prefix inside the engine diagrams are `SessionEngine` methods.

## 1. Processes, threads, and transports

```mermaid
flowchart LR
    Agent[Codex / MCP client]
    subgraph MCP[Python MCP server process]
        Tools[securecrt_server.py<br/>FastMCP tools]
        Client[bridge_client.py<br/>async RPC client]
        PolicyClient[bridge_core.py<br/>validation and policy helpers]
        Tools --> Client
        Tools --> PolicyClient
    end
    subgraph CRT[SecureCRT bridge runtime - one per shared tab]
        subgraph Workers[Socket worker threads]
            Server[BridgeServer<br/>inherited serve_forever]
            Handler[BridgeRequestHandler.handle]
            Server --> Handler
        end
        Queue[queue.Queue<br/>request, Event, response dict]
        subgraph Script[SecureCRT script thread]
            Loop[run_bridge loop]
            Service[service_requests]
            Engine[SessionEngine]
            Native[Fixed tab: Session and Screen API]
            Loop --> Service
            Service -->|dispatch| Engine
            Loop -->|pump| Engine
            Engine <--> Native
        end
        Handler -->|enqueue| Queue
        Queue -->|dequeue| Service
        Service -.->|fill response dict and set Event| Handler
    end
    Profile[(session_id.profile)]
    Agent <-->|stdio / MCP| Tools
    Client <-->|Unix socket / newline-delimited JSON| Handler
    PolicyClient -->|read selected profile| Profile
    Engine -->|read selected profile| Profile
    Loop -->|write at startup / remove at shutdown| Profile
    Native <-->|existing authenticated connection| Device[Remote device or shell]
```

The socket is `/tmp/securecrt-mcp-<uid>/<session_id>.sock`. Each RPC uses a new connection and exchanges one request and one response. Socket threads never call SecureCRT APIs. `bridge_core.py` is loaded in both runtimes, but the MCP process only uses its helpers; the engine and listener run inside SecureCRT.

## 2. Startup, profile selection, and shutdown

```mermaid
flowchart TD
    Main[securecrt_bridge.main] -->|runpy.run_path| Core[Load bridge_core.py]
    Core -->|runpy.run_path| Profiles[Load command_profiles.py]
    Main -->|runpy.run_path| Controller[Load profile_controller.py]
    Main --> Available[available_command_profiles]
    Available --> Names[COMMAND_PROFILES keys]
    Main --> Select[profile_controller.select_profile]
    Select --> Prompt[crt.Dialog.Prompt]
    Prompt -->|invalid selection| Message[crt.Dialog.MessageBox]
    Message --> Prompt
    Select -->|cancel / empty| Cancel[main: show message and return]
    Select -->|selected profile returned to main| Run[run_bridge]
    Run --> Tab[crt.GetScriptTab / Session.Connected]
    Run --> Set[set_command_profile]
    Run --> Path[_profile_file / runtime_directory]
    Run --> Init[SessionEngine.__init__]
    Init --> Settings[Save Screen settings<br/>Synchronous=false / IgnoreEscape=true]
    Run --> Server[BridgeServer with BridgeRequestHandler<br/>Queue capacity 32]
    Server --> Thread[Background thread: serve_forever]
    Run --> Loop{while not stopping}
    Loop --> Service[service_requests]
    Service --> Stop{stopping?}
    Stop -->|no| Pump[SessionEngine.pump]
    Pump --> Connected{Session.Connected?}
    Connected -->|yes| Sleep[crt.Sleep: 1 or 10 ms active / 30 ms idle]
    Sleep --> Loop
    Stop -->|yes| Cleanup[finally: cleanup]
    Connected -->|no| Cleanup
    Loop -->|loop ends| Cleanup
    Cleanup --> Restore[SessionEngine.restore]
    Restore --> Shutdown[server.shutdown / server_close]
    Shutdown --> Unlink[Remove socket and profile files]
```

An unconnected tab returns before bridge creation. The `finally` cleanup also runs on exceptions within the listener/loop block. Detaching sets `stopping`; it does not close the remote connection or send Ctrl+C.

## 3. Every MCP tool and RPC route

```mermaid
flowchart LR
    subgraph Tools[securecrt_server.py]
        List[list_sessions]
        Probe[probe_prompt]
        Run[run_command]
        Read[read_output]
        Input[send_input]
        Interrupt[interrupt_command]
        Detach[detach_session]
    end
    subgraph Client[bridge_client.py]
        Sessions[sessions]
        Inspect[inspect_session]
        Wait[wait_for_job]
        Request[request]
        Exchange[exchange]
        Dir[runtime_directory<br/>imported from bridge_core]
    end
    List --> Sessions
    Sessions -->|discover *.sock| Dir
    Sessions -->|asyncio.gather across tabs| Inspect
    Inspect -->|status| Request
    Probe -->|probe + new job_id, timeout=5| Request
    Run -->|run + new job_id + options| Request
    Probe -->|after initial RPC, wait up to 7 s| Wait
    Run -->|if wait_seconds is nonzero| Wait
    Read -->|output + cursor + limit| Request
    Input -->|input| Request
    Interrupt -->|interrupt| Request
    Detach -->|detach| Request
    Wait -->|poll output every 0.2 s while running| Request
    Request --> Dir
    Request -->|validate session ID; add expires_at; 8 s timeout| Exchange
    Exchange <-->|one JSON line each way| Handler[BridgeRequestHandler.handle]
    Handler -->|queued handoff| Service[service_requests]
    Service --> Dispatch[SessionEngine.dispatch]
    Dispatch -->|status| Status[status]
    Dispatch -->|run or probe| Start[_start]
    Dispatch -->|output| Output[output]
    Dispatch -->|input| Send[send_input]
    Dispatch -->|interrupt| Ctrl[interrupt]
    Dispatch -->|detach| Stop[set stopping=true; return detached result]
```

`wait_for_job` returns when status is no longer `running`, a manual pager is waiting, or its wait deadline is reached. `inspect_session` converts unavailable bridges into listing errors so other sessions can still be returned. `request` raises on an RPC error and never automatically retries a write.

## 4. Classes and ownership

```mermaid
classDiagram
    class StreamRequestHandler
    class ThreadingUnixStreamServer
    class BridgeRequestHandler {
        handle()
    }
    class BridgeServer {
        daemon_threads = True
        block_on_close = False
        requests
    }
    class SessionEngine {
        tab
        screen
        session_id
        version
        clock
        prompt
        jobs
        active
        stopping
        original
        __init__(tab, session_id, version, clock)
        status()
        _start(request)
        output(job_id, cursor, limit)
        _finish(status, completion)
        _append(value, received_at)
        _advance_pager(job)
        check_completion(job, text, index)
        pump()
        interrupt(request)
        send_input(request)
        dispatch(request)
        restore()
    }
    class OutputBuffer {
        blocks
        size
        truncated
        __init__()
        append(text)
        read(start, end)
    }
    class JobDictionary {
        job_id
        status
        output
        line
        prompt
        deadline
        waiting_for_input
        auto_paging
        pages_advanced
    }
    class SecureCRTTab
    StreamRequestHandler <|-- BridgeRequestHandler
    ThreadingUnixStreamServer <|-- BridgeServer
    BridgeServer --> BridgeRequestHandler : creates handlers
    BridgeRequestHandler --> BridgeServer : server.requests
    SessionEngine "1" *-- "0..10" JobDictionary : jobs OrderedDict
    JobDictionary "1" *-- "1" OutputBuffer : output
    SessionEngine --> SecureCRTTab : fixed tab reference
```

`JobDictionary` is a diagram representation of a Python dictionary, not a declared class. The other two unimplemented base classes belong to `socketserver`. `BridgeServer` defines settings only; its networking methods are inherited. An engine owns at most one active job. Each buffer retains up to 1,000,000 characters in 4,096-character blocks.

## 5. Validation and policy function calls

```mermaid
flowchart TD
    Run[securecrt_server.run_command] --> VC[validate_command]
    Run --> AC[authorize_command]
    Run -->|when supplied| VP[validate_prompt]
    Probe[securecrt_server.probe_prompt] --> Get[get_command_profile]
    Input[securecrt_server.send_input] --> AI[authorize_input]
    Start[SessionEngine._start] -->|run| VC
    Start -->|run| AC
    Start -->|probe| Get
    Start -->|non-probe| VP
    EngineInput[SessionEngine.send_input] --> AI
    Pager[SessionEngine._advance_pager] -->|space, no Enter| AI
    Status[SessionEngine.status] --> Get
    Status --> Shell[device_shell_for_profile]
    AC --> VC
    AC --> Get
    AI --> Get
    Get --> File[_profile_file]
    Set[set_command_profile] --> File
    File --> Dir[runtime_directory]
    Get -->|read and validate name| Disk[(session_id.profile)]
    Set -->|write temp, chmod 0600, atomic replace| Disk
    AC --> Config[COMMAND_PROFILES]
    AI --> Config
    Get --> Config
    Set --> Config
    Shell --> Config
    Available[available_command_profiles] --> Config
    Status --> Config
```

Validation happens in the MCP tool and again at the bridge boundary. `_start` also checks job identity, tab ownership, connection, timeout, and `auto_paging`; an already known job ID returns its existing output. `SessionEngine.send_input` checks active ownership and printable input. `runtime_directory` creates/checks the user-owned 0700 runtime directory. Full profiles allow printable commands; restricted profiles check whole-word prefixes and simple argument characters.

## 6. Engine calls and terminal output capture

```mermaid
flowchart TD
    Start[_start] --> New[OutputBuffer.__init__<br/>create job and set active]
    Start -->|enable buffering then command + Enter| Send[Screen.Send]
    Start --> Output[output]
    Pump[pump] -->|disconnected or native API exception| Finish[_finish]
    Pump --> Read[Screen.ReadString / MatchIndex]
    Read -->|chunks: newline, pager, prompt| Append[_append]
    Append --> BufferAppend[OutputBuffer.append]
    Append --> Line[Update current line and waiting_for_input]
    Pump -->|flush immediately at pager delimiter| Advance[_advance_pager]
    Advance -->|authorize; clear marker; send one space| Send
    Pump -->|after batch| Check[check_completion]
    Check -->|idle full prompt or probe result| Finish
    Check -->|deadline reached| Timeout[status=timed_out<br/>retain active job and continue capture]
    Pump -->|unexpected native read timeout| Incomplete[capture_may_be_incomplete=true]
    Finish --> Release[Set status/completion/time<br/>clear active; Synchronous=false]
    Output --> BufferRead[OutputBuffer.read]
    BufferRead --> Result[Output page + next_cursor + job metadata]
    Input[send_input] -->|validated explicit text, optional Enter| Send
    Input --> Output
    Interrupt[interrupt] -->|Ctrl+C to active job| Send
    Interrupt --> Flag[interrupted=true<br/>status=interrupt_requested]
    Interrupt --> Output
    Restore[restore] --> Settings[Restore original Synchronous and IgnoreEscape]
```

`pump` calls `_append` with collected chunks and `_advance_pager` at pager boundaries; the read node shows the data source. Reads are batched for roughly 50 ms, but an individual native read can block for one second. Completion requires stream inactivity for at least one second. Interrupt requests keep ownership until completion is detected. A failed initial `Send` leaves the job active with `send_failed_delivery_unknown` because delivery may have partially succeeded.

## 7. One command from request to result

```mermaid
sequenceDiagram
    participant A as Codex
    participant T as securecrt_server.run_command
    participant C as bridge_client
    participant H as BridgeRequestHandler.handle
    participant Q as Queue / Event / response dict
    participant L as run_bridge / service_requests
    participant E as SessionEngine
    participant S as SecureCRT Screen
    A->>T: command, session_id, options
    T->>T: validate and authorize; generate job_id
    T->>C: request(operation=run)
    C->>H: exchange: JSON over Unix socket
    H->>Q: enqueue(request, event, response)
    H->>H: wait on event (up to 6 s)
    L->>Q: dequeue (up to 32 requests per pass)
    L->>L: reject expired request before dispatch
    L->>E: dispatch → _start
    E->>E: validate; create job and buffer
    E->>S: Synchronous=true; Send(command + Enter)
    E-->>L: output snapshot
    L->>Q: write result or error; event.set()
    Q-->>H: wake worker with shared response
    H-->>C: JSON response
    C-->>T: result
    loop script loop while job remains active
        L->>E: pump()
        E->>S: ReadString(delimiters, 1)
        S-->>E: text + MatchIndex
        E->>E: _append → OutputBuffer.append
        opt standalone pager and automatic paging enabled
            E->>S: _advance_pager → Send(space)
        end
        E->>E: check_completion; possibly _finish
    end
    opt wait_seconds is nonzero
        T->>C: wait_for_job(session_id, job_id, wait_seconds)
        loop poll until status changes, pager waits, or wait expires
            C->>H: request(operation=output), same queue route
            H->>Q: enqueue output request
            L->>Q: dequeue
            L->>E: dispatch → output → OutputBuffer.read
            E-->>L: text page and job metadata
            L->>Q: write response; event.set()
            Q-->>H: wake worker
            H-->>C: JSON response
        end
        C-->>T: latest output snapshot
    end
    T-->>A: output, job_id, next_cursor, status
```

Capture and client polling interleave; the sequence separates them visually for readability. Output is retrieved by polling, not pushed to the MCP client. `read_output` follows the same output route and can supply `next_cursor` to fetch later pages. Job timeout, client wait expiry, and RPC timeout do not stop the remote command.

## 8. Tests and standalone toy example

```mermaid
flowchart LR
    subgraph Tests[Local unit tests]
        Setup[CaptureTests.setUp] --> Fake[Screen.__init__<br/>simulated clock and stream]
        Setup --> Init[SessionEngine.__init__]
        Setup --> Mock[Patch get_command_profile]
        Cases[CaptureTests.test_*] --> Start[CaptureTests.start]
        Start --> EngineStart[SessionEngine._start]
        Cases --> Pump[SessionEngine.pump]
        Cases --> Result[CaptureTests.result]
        Result --> Output[SessionEngine.output]
        Cases --> Controls[SessionEngine.interrupt / send_input]
        EngineStart --> FakeSend[Screen.Send]
        Pump --> FakeRead[Screen.ReadString]
        Pump --> FakeSend
        Controls --> FakeSend
        WaitTests[WaitTests.test_manual_pager_returns_without_waiting<br/>WaitTests.test_auto_paging_keeps_waiting_until_complete]
        WaitTests --> Wait[bridge_client.wait_for_job]
        Wait --> Mocks[AsyncMock: request and asyncio.sleep]
    end
    subgraph Toy[Independent Toy_examples scripts - no classes or functions]
        CLI[client.py top-level code] <-->|localhost TCP port 8765<br/>request line / reply until EOF| Bridge[bridge.py top-level loop]
        Bridge -->|send| Send[tab.Screen.Send]
        Bridge -->|read| Get[tab.Screen.Get: visible screen]
        Bridge -->|stop| Close[Exit loop and close server socket]
    end
```

The capture tests cover paging, prompt discovery, incomplete capture, interruption, send failure, cursor reads, truncation, and invalid options. They use a simulated `Screen`, not SecureCRT. The toy example is separate from MCP: it has no job engine, profile checks, or Unix-socket queue architecture.

## Source files

- [securecrt_server.py](securecrt_server.py): MCP tools and stdio entry point.
- [bridge_client.py](bridge_client.py): discovery, JSON RPC, and polling.
- [securecrt_bridge.py](securecrt_bridge.py): SecureCRT entry point.
- [bridge_core.py](bridge_core.py): policy helpers, socket server, engine, and buffers.
- [profile_controller.py](profile_controller.py): profile selection dialog.
- [command_profiles.py](command_profiles.py): shell declarations and command permissions.
- [test_bridge_core.py](test_bridge_core.py) and [test_bridge_client.py](test_bridge_client.py): simulated tests.
- [Toy_examples](Toy_examples/README.md): independent beginner example.
