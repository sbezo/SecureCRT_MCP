# $language = "Python"
# $interface = "1.0"

import select  # Lets us check whether a client is waiting to connect.
import socket  # Lets Python communicate through a network connection.

tab = crt.GetScriptTab()  # Remember the SecureCRT tab where this script was started.
server = socket.socket()  # Create a TCP socket that will accept client connections.
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow reuse of the local address after a previous run closes.
server.bind(("127.0.0.1", 8765))  # Use port 8765 on this computer only (localhost).
server.listen(1)  # Start listening; allow one connection to wait in the queue.

running = True  # Keep the bridge running until we receive "stop".
while running:  # Repeat the indented code while running is True.
    # Give SecureCRT time to update its screen while waiting for a client.
    ready, unused, unused = select.select([server], [], [], 0)  # Check immediately for a waiting client; ignore the other two results.
    if ready:  # Handle a connection only when the ready list is not empty.
        client, address = server.accept()  # Accept the connection; client is its socket, address identifies the caller.
        stream = client.makefile("r", encoding="utf-8")  # Wrap the socket in a text reader; no disk file is created.
        request = stream.readline().rstrip("\n")  # Wait for one request line, then remove its ending newline.
        action, separator, text = request.partition(" ")  # Split at the first space: "send whoami" becomes "send", " ", "whoami".

        reply = "OK"  # Default response to send back to the client.
        if action == "send":  # If the request starts with "send", type its remaining text.
            tab.Screen.Send(text + "\r")  # Send the text to the remote session; \r presses Enter.
        elif action == "read":  # Otherwise, if the request is "read", capture the visible screen.
            reply = tab.Screen.Get(1, 1, tab.Screen.Rows, tab.Screen.Columns)  # Read from row 1, column 1 to the bottom-right corner.
        elif action == "stop":  # Otherwise, if the request is "stop", finish the bridge.
            running = False  # Prevent another loop iteration after this reply is sent.

        client.sendall((reply + "\n").encode("utf-8"))  # Add a newline, convert the reply to bytes, and send all of it.
        stream.close()  # Close the text reader created by makefile().
        client.close()  # Close this client's connection so its read() knows the reply is finished.

    crt.Sleep(50)  # Pause for 50 milliseconds and let SecureCRT process its screen updates.

server.close()  # Stop listening on the port after the loop ends.
