import socket  # Lets Python communicate through a network connection.
import sys  # Gives us the arguments typed after the script name in the terminal.

# Turn command-line arguments into one request, for example: send whoami
request = " ".join(sys.argv[1:])  # Skip the script name and join the remaining arguments with spaces, such as "send whoami".
client = socket.create_connection(("127.0.0.1", 8765))  # Connect to the bridge listening on port 8765 on this computer.
client.sendall((request + "\n").encode("utf-8"))  # End the request with a newline, convert it to bytes, and send all of it.

stream = client.makefile("r", encoding="utf-8")  # Wrap the socket in a text reader that converts incoming bytes to text; no disk file is created.
print(stream.read(), end="")  # Read until the bridge closes its connection, then print the reply without adding another newline.
stream.close()  # Close the text reader created by makefile().
client.close()  # Close our socket connection to the bridge.
