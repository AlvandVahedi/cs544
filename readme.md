# Simple Chat Protocol (SCP) Implementation

This project implements the Simple Chat Protocol (SCP) v1.0, a stateful application protocol over QUIC for one-to-one text-based chat.

## Files

* `scp_constants.py`: Defines protocol constants (message types, states, status codes).
* `pdu.py`: Handles SCP Protocol Data Unit (PDU) structures, packing, and unpacking.
* `server.py`: The SCP server using `aioquic`.
* `client.py`: The SCP client using `aioquic`.
* `readme.md`: This file.

## Prerequisites

* Python 3.7+
* `aioquic` library: Install using `pip install aioquic`
* OpenSSL (for generating SSL certificates for the server)

## Setup

1.  **Generate SSL Certificate for Server**:
    QUIC requires a secure connection. You need to generate a self-signed certificate and private key for the server. Place these files (`cert.pem` and `privkey.pem`) in the same directory as `server.py`.
    ```bash
    openssl req -x509 -newkey rsa:2048 -keyout privkey.pem -out cert.pem -days 365 -nodes
    ```
    When prompted, you can enter any information or leave fields blank.

## Running the Application

1.  **Start the Server**:
    Open a terminal and run:
    ```bash
    python server.py
    ```
    The server will start listening on `0.0.0.0:4433` by default.

2.  **Start the Client(s)**:
    Open one or more new terminals to run client instances.
    ```bash
    python client.py <your_username> <server_ip_or_hostname> [server_port]
    ```
    * `<your_username>`: The username you want to use for the chat. 
    * `<server_ip_or_hostname>`: The IP address or hostname of the machine running the server (e.g., `localhost` or `127.0.0.1` if running on the same machine). 
    * `[server_port]`: Optional. The port the server is listening on. Defaults to `4433`.

    Example:
    ```bash
    python client.py alice localhost
    ```
    In another terminal:
    ```bash
    python client.py bob localhost
    ```

## Client Commands 

Once connected, the client accepts the following commands:

* `/chat <peer_username>`: Request to start a chat with another connected user. 
    * Example: `/chat bob`
* `/accept <peer_username>`: Accept an incoming chat request from `<peer_username>`. 
    * Example: `/accept alice`
* `/reject <peer_username>`: Reject an incoming chat request from `<peer_username>`. 
    * Example: `/reject alice`
* `<your message>`: If you are in a chat, typing any text that doesn't start with `/` will send it as a message to your chat partner.
* `/endchat`: Ends the current chat session (sends a disconnect to the server).
* `/disconnect`: Disconnects from the server.

## Protocol Features Implemented

* Client connection to the server using QUIC. 
* Simple username-based login (no password).
* Server tracks connected users.
* Client can request to chat with another connected user.
* Server forwards the chat request to the target client. 
* Target client can accept or reject the request. 
* If accepted, clients can exchange text messages via the server.
* Either client can disconnect and end the chat (notifying the peer). 
* Minimal error handling.
* Messages follow the defined binary PDU format.
* Stateful protocol implementation for both client and server (DFAs).
