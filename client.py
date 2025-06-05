import asyncio
import logging
import sys
from typing import Optional
import ssl
from functools import partial

from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent, StreamDataReceived, ConnectionTerminated, HandshakeCompleted

from scp_constants import (
    SCPMessageType, SCPClientState, SERVER_PORT, SERVER_HOST, HEADER_SIZE,
    SCP_CONNECT_SUCCESS, SCP_CHAT_INIT_FORWARDED, SCP_CHAT_FWD_ACCEPTED, SCP_CHAT_FWD_REJECTED,
    SCP_ERR_UNEXPECTED_MSG_TYPE
)
from pdu import (
    SCPHeader, ConnectReqPDU, ChatInitReqPDU, ChatFwdRespPDU, TextPDU,
    DisconnectReqPDU, PDU_UNPACKERS
)


class SCPClientProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        # Pop custom kwarg 'username_to_log_in' before passing to super
        self.username: Optional[str] = kwargs.pop('username_to_log_in', None)
        super().__init__(*args, **kwargs)

        if not self.username:
            # This should ideally not happen if username is passed correctly
            logging.error("Client: Username not provided to protocol constructor!")
            self.client_state: SCPClientState = SCPClientState.DISCONNECTED
        else:
            self.client_state: SCPClientState = SCPClientState.CONNECTING

        self.current_chat_target: Optional[str] = None
        self.pending_fwd_from: Optional[str] = None
        self._stream_id: Optional[int] = None
        self.ui_event_loop = asyncio.get_event_loop()
        self.user_input_task = None
        logging.info(f"Client protocol initialized for user '{self.username}', state: {self.client_state.name}")

    def _ensure_stream(self):
        if self._stream_id is None:
            self._stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
            if self._stream_id is None: # If no existing stream, try to create one
                 try:
                    self._stream_id = self._quic.create_stream(is_unidirectional=False)
                    logging.info(f"Created client stream: {self._stream_id}")
                 except Exception as e:
                    logging.error(f"Client could not create stream: {e}")
                    self._stream_id = None # Ensure it's None if creation failed
        return self._stream_id

    def send_pdu(self, pdu_data: bytes):
        stream_id = self._ensure_stream()
        if stream_id is not None:
            self._quic.send_stream_data(stream_id, pdu_data, end_stream=False)
            self.transmit()
            logging.debug(f"Client sent PDU on stream {stream_id}")
        else:
            logging.error("Client: No stream available to send PDU.")
            # Potentially try to reconnect or signal error to user
            self.client_state = SCPClientState.DISCONNECTED
            print("Connection error: Could not get a stream. Disconnecting.")
            if self.user_input_task:
                self.user_input_task.cancel()

    def quic_event_received(self, event: QuicEvent):
        if isinstance(event, HandshakeCompleted):
            logging.info("Client: QUIC Handshake completed.")
            self._ensure_stream()
            # Username is set in __init__, state is already CONNECTING
            if self.client_state == SCPClientState.CONNECTING and self.username:
                self._send_connect_req()
            elif not self.username:
                logging.error(
                    "Client: Handshake completed, but no username is set on protocol. Cannot send CONNECT_REQ.")
            else:
                logging.warning(
                    f"Client: Handshake completed, but not in CONNECTING state (State: {self.client_state.name}). Not sending CONNECT_REQ.")

        elif isinstance(event, StreamDataReceived):
            logging.info(f"Client: StreamDataReceived on stream {event.stream_id}: {event.data}")
            try:
                header = SCPHeader.unpack(event.data[:HEADER_SIZE])
                payload = event.data[HEADER_SIZE : HEADER_SIZE + header.payload_length]

                if header.payload_length != len(payload):
                    logging.warning(f"Client: Invalid payload length from server. Expected {header.payload_length}, got {len(payload)}")
                    return 

                unpack_func = PDU_UNPACKERS.get(header.message_type)
                if not unpack_func:
                    logging.warning(f"Client: Unknown message type {header.message_type} from server")
                    return
                
                pdu = unpack_func(payload)
                self.handle_scp_message(header.message_type, pdu)

            except ValueError as e:
                logging.error(f"Client: Malformed message from server: {e}, data: {event.data}")
            except Exception as e:
                logging.error(f"Client: Error processing message from server: {e}")


        elif isinstance(event, ConnectionTerminated):
            logging.info(f"Client: Connection terminated. Reason: {event.reason_phrase if event.reason_phrase else 'N/A'}")
            self.client_state = SCPClientState.DISCONNECTED
            print("\nDisconnected from server.")
            if self.user_input_task:
                self.user_input_task.cancel()


    def handle_scp_message(self, msg_type: SCPMessageType, pdu):
        logging.info(f"Client: Handling {msg_type.name} from server in state {self.client_state.name}")

        if msg_type == SCPMessageType.CONNECT_RESP:
            if self.client_state == SCPClientState.CONNECTING:
                self.handle_connect_resp(pdu)
            else:
                self._log_unexpected_msg(msg_type)
        
        elif msg_type == SCPMessageType.CHAT_INIT_RESP:
            # Received after we sent CHAT_INIT_REQ
            if self.client_state == SCPClientState.INITIATING_CHAT: # ("recv CHAT_INIT_RESP (Fail/Busy/Rej)" or "recv CHAT_INIT_RESP (Forwarded)")
                self.handle_chat_init_resp(pdu)
            else:
                self._log_unexpected_msg(msg_type)

        elif msg_type == SCPMessageType.CHAT_FWD_REQ: # Server wants to forward a chat request to us
            if self.client_state == SCPClientState.IDLE: # diagram shows transition from Idle
                self.handle_chat_fwd_req(pdu)
            else:
                self._log_unexpected_msg(msg_type)
        
        elif msg_type == SCPMessageType.TEXT:
            # Can be a regular chat message or a server notification (like chat started)
            self.handle_text(pdu) # Display text if IN_CHAT or AWAITING_PEER_RESPONSE
                                  # Client DFA (Fig 1) shows recv ServNotif (Peer Acc) from AwaitingPeerResp to InChat
                                  # Also send/recv TEXT in InChat state.
        
        elif msg_type == SCPMessageType.DISCONNECT_NOTIF: # Peer disconnected
            if self.client_state == SCPClientState.IN_CHAT:
                self.handle_disconnect_notif(pdu)
            else:
                self._log_unexpected_msg(msg_type)

        elif msg_type == SCPMessageType.ERROR:
            self.handle_error(pdu)
        
        else:
            logging.warning(f"Client: Unhandled message type {msg_type.name} in state {self.client_state.name}")

    def _log_unexpected_msg(self, msg_type):
        logging.warning(f"Client: Received unexpected {msg_type.name} in state {self.client_state.name}")
        # Client doesn't send ERROR PDU for this by default, but could. Minimal error handling

    def _send_connect_req(self):
        if self.username:
            pdu = ConnectReqPDU(self.username).pack()
            self.send_pdu(pdu)
            logging.info(f"Client: Sent CONNECT_REQ for user {self.username}")

    def handle_connect_resp(self, pdu):
        if pdu.status_code == SCP_CONNECT_SUCCESS:
            self.client_state = SCPClientState.IDLE # (Connected to server, not in chat)
            print(f"Successfully connected as '{self.username}'. You are IDLE.")
            print("Commands: /chat <peer_username>, /disconnect")
        else:
            self.client_state = SCPClientState.DISCONNECTED
            print(f"Connection failed. Status: {pdu.status_code}. Please restart client.")
            # Terminate connection or allow retry
            self.close()
            if self.user_input_task: self.user_input_task.cancel()

    def handle_chat_init_resp(self, pdu): # Response to our CHAT_INIT_REQ
        if pdu.status_code == SCP_CHAT_INIT_FORWARDED:
            self.client_state = SCPClientState.AWAITING_PEER_RESPONSE
            print(f"Chat request for '{self.current_chat_target}' forwarded. Waiting for peer's response...")
        else:
            self.client_state = SCPClientState.IDLE # (Fail/Busy/Rej)
            print(f"Chat initiation with '{self.current_chat_target}' failed. Server response: {pdu.status_code}")
            self.current_chat_target = None

    def handle_chat_fwd_req(self, pdu): # Someone wants to chat with us
        self.pending_fwd_from = pdu.originator_username
        self.client_state = SCPClientState.PENDING_PEER_ACCEPT # ("recv CHAT_FWD_REQ")
        print(f"\nUser '{self.pending_fwd_from}' wants to chat with you.")
        print(f"Type /accept {self.pending_fwd_from} or /reject {self.pending_fwd_from}")

    def handle_text(self, pdu):
        # This message might also signal chat start as per server's simplified notification
        if self.client_state == SCPClientState.AWAITING_PEER_RESPONSE and self.current_chat_target:
            # Assuming text like "Chat with X started." means peer accepted
            # Client DFA (Fig 1) has "recv ServNotif (Peer Acc)" from AwaitingPeerResp to InChat
            if "started" in pdu.text_message.lower() and self.current_chat_target in pdu.text_message:
                 self.client_state = SCPClientState.IN_CHAT
                 print(f"\n{pdu.text_message}")
                 print(f"You are now IN_CHAT with {self.current_chat_target}. Type messages or /endchat.")
                 return

        if self.client_state == SCPClientState.IN_CHAT:
            print(f"\n{pdu.text_message}") # Display message
        elif self.client_state == SCPClientState.IDLE and "Chat with" in pdu.text_message and "started" in pdu.text_message:
            # This handles the case where the client accepted a chat and server sends confirmation
            # Extract peer name (a bit hacky based on server message format)
            try:
                parts = pdu.text_message.split(" ")
                peer_name = parts[parts.index("with")+1]
                if peer_name.endswith('.'): peer_name = peer_name[:-1]
                self.current_chat_target = peer_name
                self.client_state = SCPClientState.IN_CHAT
                print(f"\n{pdu.text_message}")
                print(f"You are now IN_CHAT with {self.current_chat_target}. Type messages or /endchat.")
            except (ValueError, IndexError):
                 print(f"\nNotification: {pdu.text_message}") # Generic notification
        else:
            print(f"\nNotification: {pdu.text_message}") # Could be an error message or unexpected TEXT

    def handle_disconnect_notif(self, pdu):
        print(f"\nUser '{pdu.peer_username}' has disconnected from the chat.")
        self.client_state = SCPClientState.IDLE # diagram ("recv DISCONNECT_NOTIF (PeerLeft)")
        self.current_chat_target = None
        print("You are now IDLE. Commands: /chat <peer_username>, /disconnect")


    def handle_error(self, pdu):
        print(f"\nServer Error: Code {pdu.error_code}, Message: {pdu.error_message}")
        # Based on error, client might change state or disconnect
        if pdu.error_code == SCP_ERR_UNEXPECTED_MSG_TYPE:
            # Potentially reset state if out of sync
            pass


    # # --- User initiated actions ---
    # def user_connect(self, username_to_connect: str):
    #     if self.client_state == SCPClientState.DISCONNECTED:
    #         self.username = username_to_connect
    #         self.client_state = SCPClientState.CONNECTING # Set before _send_connect_req is called by handshake_completed
    #         # Connection is established by the main 'connect' call. HandshakeCompleted will trigger _send_connect_req.
    #         logging.info(f"Client: Attempting to connect as {self.username} (state set to CONNECTING).")
    #         # Actual PDU send will be in HandshakeCompleted
    #     else:
    #         print("Already connected or connecting.")

    def user_initiate_chat(self, peer_username: str):
        if self.client_state == SCPClientState.IDLE:
            self.current_chat_target = peer_username
            pdu = ChatInitReqPDU(peer_username).pack()
            self.send_pdu(pdu)
            self.client_state = SCPClientState.INITIATING_CHAT
            print(f"Sent chat request to '{peer_username}'.")
        else:
            print(f"Cannot initiate chat. Current state: {self.client_state.name}")

    def user_respond_to_chat(self, accept: bool, originator_username: str):
        if self.client_state == SCPClientState.PENDING_PEER_ACCEPT and self.pending_fwd_from == originator_username:
            status = SCP_CHAT_FWD_ACCEPTED if accept else SCP_CHAT_FWD_REJECTED
            pdu = ChatFwdRespPDU(status, originator_username).pack()
            self.send_pdu(pdu)
            
            if accept:
                # Server will confirm and move us to IN_CHAT typically via a notification/first message
                # For now, client optimistically assumes it will go to IDLE and wait for server.
                # The server should transition both to IN_CHAT and perhaps send a confirmation.
                # Client diagram shows transition directly to InChat from PendingAccept if user accepts
                # However, this relies on server confirming. We will move to IDLE here,
                # and server TEXT message (e.g. "Chat started") will move to IN_CHAT.
                # This is slightly different than the diagram but simpler for now until a specific "chat started" PDU.
                self.client_state = SCPClientState.IDLE # Wait for server to confirm and move to IN_CHAT
                self.current_chat_target = originator_username # Tentatively set
                print(f"Responded {'accept' if accept else 'reject'} to {originator_username}. Waiting for server confirmation.")
            else:
                self.client_state = SCPClientState.IDLE # diagram ("User: reject")
                print(f"Rejected chat with {originator_username}.")
            self.pending_fwd_from = None
        else:
            print(f"No pending chat request from '{originator_username}' or invalid state ({self.client_state.name}).")

    def user_send_text(self, message: str):
        if self.client_state == SCPClientState.IN_CHAT:
            pdu = TextPDU(message).pack()
            self.send_pdu(pdu)
        else:
            print(f"Cannot send message. Not in a chat (State: {self.client_state.name}).")

    def user_end_chat(self): # Client wishes to end current chat, but stay connected to server
        if self.client_state == SCPClientState.IN_CHAT:
            # SCP v1.0 doesn't have a specific "end chat but stay connected" PDU.
            # It uses DISCONNECT_REQ to leave server or implies server handles if one peer disconnects.
            # For this, we'll make it behave like a full DISCONNECT_REQ for simplicity here,
            # or the user can just stop sending messages.
            # A proper implementation would require a CHAT_CLOSE_REQ or similar.
            # Let's interpret "/endchat" as client just wants to go IDLE locally.
            # If server needs to know, a DISCONNECT_REQ would be sent for the session.
            # The proposal implies "Either client can disconnect and end the chat."
            # and then "The server notifies the peer if the other client disconnects."
            # So sending DISCONNECT_REQ is the way.
            print("Ending chat by sending DISCONNECT_REQ...")
            self.user_disconnect()
        else:
            print("Not currently in a chat.")


    def user_disconnect(self): # Disconnect from server
        if self.client_state not in [SCPClientState.DISCONNECTED, SCPClientState.DISCONNECTING]:
            pdu = DisconnectReqPDU().pack()
            self.send_pdu(pdu)
            self.client_state = SCPClientState.DISCONNECTING
            print("Sent disconnect request to server.")
            # Connection will be terminated by server or aioquic.
            # self.close() # This can be called here, or wait for ConnectionTerminated
        else:
            print("Not connected or already disconnecting.")


async def amain(client: SCPClientProtocol):
    """Main async function to handle user input."""
    # client.user_connect(username_to_connect) # Set username and state, actual connect PDU on handshake

    if client.client_state != SCPClientState.CONNECTING and client.client_state != SCPClientState.IDLE:
        if client.client_state == SCPClientState.DISCONNECTED and not client.username:
             logging.error("Client is disconnected (no username). Cannot start UI loop.")
             return
        elif client.client_state == SCPClientState.DISCONNECTED:
             logging.warning(f"Client {client.username} is DISCONNECTED. UI loop may not function.")
             # Allow it to proceed to see if it reconnects or exits gracefully based on user input /disconnect.

    while client.client_state != SCPClientState.DISCONNECTED:
        try:
            if sys.stdin.isatty(): # only show prompt in interactive mode
                 prompt_prefix = f"({client.client_state.name}) {client.username or ''}{'@' + client.current_chat_target if client.current_chat_target else ''} > "
                 raw_line = await asyncio.get_event_loop().run_in_executor(None, lambda: input(prompt_prefix))
            else: # for piping commands or non-interactive use
                raw_line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
                if not raw_line: # EOF
                    if client.client_state != SCPClientState.DISCONNECTED:
                        client.user_disconnect()
                    break 
            
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line.split(" ", 2)
                command = parts[0]
                args = parts[1:]

                if command == "/chat" and len(args) == 1:
                    client.user_initiate_chat(args[0])
                elif command == "/accept" and len(args) == 1:
                    client.user_respond_to_chat(True, args[0])
                elif command == "/reject" and len(args) == 1:
                    client.user_respond_to_chat(False, args[0])
                elif command == "/endchat":
                    client.user_end_chat()
                elif command == "/disconnect":
                    client.user_disconnect()
                    # Break loop after sending disconnect, actual close handled by ConnectionTerminated
                    # To prevent input() blocking after close:
                    await asyncio.sleep(0.1) # give time for PDU to send
                    if client.user_input_task: client.user_input_task.cancel() # Cancel self
                    break 
                else:
                    print(f"Unknown command: {command}")
            elif client.client_state == SCPClientState.IN_CHAT:
                client.user_send_text(line)
            elif client.client_state == SCPClientState.PENDING_PEER_ACCEPT:
                print(f"You have a pending chat request from '{client.pending_fwd_from}'. Use /accept or /reject.")
            else:
                print("Not in a chat. Use /chat <username> to start or /disconnect.")

        except (EOFError, KeyboardInterrupt):
            if client.client_state != SCPClientState.DISCONNECTED:
                client.user_disconnect()
            break
        except asyncio.CancelledError:
            logging.info("Client input task cancelled.")
            break
        except Exception as e:
            logging.error(f"Client input loop error: {e}")
            if client.client_state != SCPClientState.DISCONNECTED:
                client.user_disconnect()
            break
    
    if client.client_state != SCPClientState.DISCONNECTED: # If loop exited for other reasons
        client.close()
    logging.info("Client input loop finished.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - CLIENT - %(levelname)s - %(message)s",
    )

    if len(sys.argv) < 3:
        print("Usage: python client.py <username> <server_host_or_ip> [server_port]")
        sys.exit(1)

    username = sys.argv[1]
    server_host = sys.argv[2]
    server_port = int(sys.argv[3]) if len(sys.argv) > 3 else SERVER_PORT

    configuration = QuicConfiguration(
        alpn_protocols=["scp-v1"],
        is_client=True,
        max_datagram_frame_size=65536,
        idle_timeout=600,
    )
    # For simplicity, client does not verify server certificate.
    # In a real scenario, load trusted CAs: configuration.load_verify_locations(cafile="pycacert.pem")
    configuration.verify_mode = ssl.CERT_NONE # NOT SECURE FOR PRODUCTION

    loop = asyncio.get_event_loop()
    try:
        async def run_client():
            protocol_factory = partial(SCPClientProtocol, username_to_log_in=username)
            async with connect(
                server_host,
                server_port,
                configuration=configuration,
                create_protocol=protocol_factory,
            ) as client_protocol:
                if client_protocol:
                    if client_protocol.client_state == SCPClientState.DISCONNECTED:
                        logging.error(
                            f"Client protocol for {username} failed to initialize correctly (is in DISCONNECTED state). Aborting.")
                        return  # Don't proceed if protocol could not be set up with username

                    # Start the user input task
                    # The amain function no longer needs to be passed the username for initial connection
                    client_protocol.user_input_task = asyncio.create_task(
                        amain(client_protocol)  # Pass only client_protocol
                    )
                    try:
                        await client_protocol.user_input_task
                    except asyncio.CancelledError:
                        logging.info("Client's amain task was cancelled.")  # Handle cancellation
                else:
                    logging.error("Failed to establish QUIC connection or create protocol.")
        
        loop.run_until_complete(run_client())

    except ConnectionRefusedError:
        logging.error(f"Connection refused by server {server_host}:{server_port}. Is server running?")
    except KeyboardInterrupt:
        logging.info("Client shutting down...")
    except Exception as e:
        logging.error(f"Client encountered an error: {e}")
    finally:
        loop.close()
        logging.info("Client event loop closed.")
