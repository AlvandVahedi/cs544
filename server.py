import asyncio
import logging
import os
from typing import Dict, Optional, Tuple, Set
from collections import defaultdict

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent, StreamDataReceived, ConnectionTerminated

from scp_constants import (
    SCPMessageType, SCPServerState, SERVER_PORT, HEADER_SIZE,
    SCP_CONNECT_SUCCESS, SCP_CONNECT_ERR_USER_EXISTS, SCP_CONNECT_ERR_SERVER_FULL,
    SCP_CHAT_INIT_FORWARDED, SCP_CHAT_INIT_ERR_PEER_NF, SCP_CHAT_INIT_ERR_PEER_BUSY,
    SCP_CHAT_INIT_ERR_SELF_CHAT, SCP_CHAT_FWD_ACCEPTED, SCP_CHAT_FWD_REJECTED,
    SCP_ERR_MALFORMED_MSG, SCP_ERR_UNEXPECTED_MSG_TYPE, SCP_ERR_INTERNAL_SERVER_ERR
)
from pdu import (
    SCPHeader, ConnectRespPDU, ChatInitRespPDU, ChatFwdReqPDU, TextPDU,
    DisconnectNotifPDU, ErrorPDU, PDU_UNPACKERS
)

# Globals for server state
# For simplicity, storing users and chats in global dicts.
# A more robust server would use a dedicated class structure.
# (host, port): SCPServerProtocol instance
connected_clients: Dict[Tuple[str, int], 'SCPServerProtocol'] = {}
# username: SCPServerProtocol instance
active_users: Dict[str, 'SCPServerProtocol'] = {} # [cite: 3]
# (user1_addr, user2_addr): stream_id for chat
# or more simply, each client object can store its peer and chat stream
chat_sessions: Dict[SCPServerProtocol, SCPServerProtocol] = {} # Maps a client to its chat partner

MAX_CLIENTS = 10 # Example limit

class SCPServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_state: SCPServerState = SCPServerState.AUTHENTICATING # [cite: 71]
        self.username: Optional[str] = None
        self.current_chat_partner: Optional[SCPServerProtocol] = None
        self.pending_chat_request_to: Optional[SCPServerProtocol] = None # User this client wants to chat with
        self.pending_chat_request_from: Optional[SCPServerProtocol] = None # User who wants to chat with this client

    def _get_client_key(self) -> Optional[Tuple[str, int]]:
        if self._quic.network_path:
            return self._quic.network_path.addr
        return None

    def send_pdu(self, pdu_data: bytes, stream_id: Optional[int] = None):
        if stream_id is None:
            # Find first available bidirectional stream or create one.
            # For SCP, we assume one primary stream is used after connection.
            # This logic might need to be more robust based on aioquic stream handling.
            stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False)
            if stream_id is None and self._quic.get_next_available_stream_id(is_unidirectional=True) is None: # hacky check if we can create
                try: # Try to create a stream if none exists
                    stream_id = self._quic.create_stream(is_unidirectional=False)
                except Exception as e:
                    logging.error(f"Cannot create stream to send PDU: {e}")
                    return


        if stream_id is not None:
            self._quic.send_stream_data(stream_id, pdu_data, end_stream=False)
            self.transmit() # Ensure data is sent
            logging.debug(f"Sent PDU on stream {stream_id} to {self.username or self._get_client_key()}")
        else:
            logging.warning(f"No stream available to send PDU to {self.username or self._get_client_key()}")


    def quic_event_received(self, event: QuicEvent):
        client_key = self._get_client_key()
        
        if isinstance(event, StreamDataReceived):
            logging.info(f"StreamDataReceived from {self.username or client_key} on stream {event.stream_id}: {event.data}")
            try:
                header_data = event.data[:HEADER_SIZE]
                header = SCPHeader.unpack(header_data)
                payload = event.data[HEADER_SIZE : HEADER_SIZE + header.payload_length]

                if header.payload_length != len(payload):
                    logging.warning(f"Invalid payload length from {self.username or client_key}. Expected {header.payload_length}, got {len(payload)}")
                    err_pdu = ErrorPDU(SCP_ERR_MALFORMED_MSG, "Invalid payload length").pack()
                    self.send_pdu(err_pdu, event.stream_id)
                    return

                unpack_func = PDU_UNPACKERS.get(header.message_type)
                if not unpack_func:
                    logging.warning(f"Unknown message type {header.message_type} from {self.username or client_key}")
                    err_pdu = ErrorPDU(SCP_ERR_MALFORMED_MSG, "Unknown message type").pack()
                    self.send_pdu(err_pdu, event.stream_id)
                    return
                
                pdu = unpack_func(payload)
                self.handle_scp_message(header.message_type, pdu, event.stream_id)

            except ValueError as e: # Catches SCPHeader.unpack errors or PDU unpack errors
                logging.error(f"Malformed message from {self.username or client_key}: {e}, data: {event.data}")
                err_pdu = ErrorPDU(SCP_ERR_MALFORMED_MSG, str(e)).pack()
                self.send_pdu(err_pdu, event.stream_id)
            except Exception as e:
                logging.error(f"Error processing message from {self.username or client_key}: {e}")
                err_pdu = ErrorPDU(SCP_ERR_INTERNAL_SERVER_ERR, "Server error processing message").pack()
                self.send_pdu(err_pdu, event.stream_id)


        elif isinstance(event, ConnectionTerminated):
            logging.info(f"Connection terminated for {self.username or client_key}")
            self.cleanup_client()

    def handle_scp_message(self, msg_type: SCPMessageType, pdu, stream_id: int):
        logging.info(f"Handling {msg_type.name} from {self.username or self._get_client_key()} in state {self.client_state.name}")

        # Centralized state validation before message-specific handlers
        # This implements parts of [cite: 82, 83, 120]

        if msg_type == SCPMessageType.CONNECT_REQ:
            if self.client_state == SCPServerState.AUTHENTICATING: # [cite: 71]
                self.handle_connect_req(pdu, stream_id)
            else:
                self._send_unexpected_msg_error(stream_id)
        
        elif self.username is None: # Must be connected for other messages
            logging.warning(f"Message {msg_type.name} from unauthenticated user {self._get_client_key()}")
            # Optionally send an error or just close
            self.close() # Terminate connection with unauthenticated user sending non-CONNECT_REQ
            return

        elif msg_type == SCPMessageType.CHAT_INIT_REQ: # [cite: 3]
            if self.client_state == SCPServerState.IDLE: # [cite: 73]
                self.handle_chat_init_req(pdu, stream_id)
            else:
                self._send_unexpected_msg_error(stream_id)
        
        elif msg_type == SCPMessageType.CHAT_FWD_RESP: # [cite: 5]
            if self.client_state == SCPServerState.AWAITING_CHAT_RESPONSE: # [cite: 75]
                self.handle_chat_fwd_resp(pdu, stream_id)
            else:
                self._send_unexpected_msg_error(stream_id)

        elif msg_type == SCPMessageType.TEXT: # [cite: 6]
            if self.client_state == SCPServerState.IN_CHAT: # [cite: 76]
                self.handle_text(pdu, stream_id)
            else:
                self._send_unexpected_msg_error(stream_id)

        elif msg_type == SCPMessageType.DISCONNECT_REQ: # [cite: 7]
            # Can be received in IDLE or IN_CHAT usually
            self.handle_disconnect_req(stream_id)
        
        else:
            logging.warning(f"Unhandled message type {msg_type.name} in state {self.client_state.name}")
            self._send_unexpected_msg_error(stream_id)


    def _send_unexpected_msg_error(self, stream_id: int):
        logging.warning(f"Unexpected message for state {self.client_state.name} from {self.username}")
        err_pdu = ErrorPDU(SCP_ERR_UNEXPECTED_MSG_TYPE, f"Unexpected message in state {self.client_state.name}").pack()
        self.send_pdu(err_pdu, stream_id)


    def handle_connect_req(self, pdu, stream_id: int): # [cite: 2, 18]
        username = pdu.username
        logging.info(f"CONNECT_REQ from {self._get_client_key()} for username '{username}'")

        status = SCP_CONNECT_SUCCESS
        if username in active_users:
            status = SCP_CONNECT_ERR_USER_EXISTS # [cite: 35]
            logging.warning(f"Username '{username}' already exists.")
        elif len(active_users) >= MAX_CLIENTS:
            status = SCP_CONNECT_ERR_SERVER_FULL # [cite: 35]
            logging.warning("Server is full.")
        else:
            # Simplified: no actual auth, just username registration
            self.username = username
            self.client_state = SCPServerState.IDLE # [cite: 72]
            active_users[username] = self
            client_key = self._get_client_key()
            if client_key:
                connected_clients[client_key] = self
            logging.info(f"User '{username}' connected successfully from {client_key}. Now in IDLE state.")

        resp_pdu = ConnectRespPDU(status).pack()
        self.send_pdu(resp_pdu, stream_id)

        if status != SCP_CONNECT_SUCCESS:
            self.close() # Close connection if login failed

    def handle_chat_init_req(self, pdu, stream_id: int): # [cite: 4]
        peer_username = pdu.peer_username
        logging.info(f"User '{self.username}' wants to chat with '{peer_username}' (CHAT_INIT_REQ)")

        status = -1
        target_client: Optional[SCPServerProtocol] = active_users.get(peer_username)

        if peer_username == self.username:
            status = SCP_CHAT_INIT_ERR_SELF_CHAT # [cite: 35]
        elif not target_client:
            status = SCP_CHAT_INIT_ERR_PEER_NF # [cite: 35]
        elif target_client.client_state != SCPServerState.IDLE: # Check if target is available (not in another chat or busy)
            status = SCP_CHAT_INIT_ERR_PEER_BUSY # [cite: 35]
        else:
            # Forward request to target client
            fwd_req_pdu = ChatFwdReqPDU(self.username).pack() # [cite: 54]
            # Find an appropriate stream for the target client.
            # This assumes target_client also has an active primary stream.
            target_stream_id = target_client._quic.get_next_available_stream_id(is_unidirectional=False)
            if target_stream_id is None: # Try to create one
                 target_stream_id = target_client._quic.create_stream(is_unidirectional=False)

            if target_stream_id is not None:
                target_client.send_pdu(fwd_req_pdu, target_stream_id)
                target_client.client_state = SCPServerState.AWAITING_CHAT_RESPONSE # [cite: 75]
                target_client.pending_chat_request_from = self
                
                self.client_state = SCPServerState.AWAITING_PEER_FOR_INIT # [cite: 74]
                self.pending_chat_request_to = target_client
                status = SCP_CHAT_INIT_FORWARDED # [cite: 35]
                logging.info(f"Forwarded CHAT_FWD_REQ from '{self.username}' to '{peer_username}'. '{self.username}' is AWAITING_PEER_FOR_INIT. '{peer_username}' is AWAITING_CHAT_RESPONSE.")
            else:
                status = SCP_CHAT_INIT_ERR_PEER_BUSY # Could interpret as cannot reach peer's stream
                logging.warning(f"Could not find/create stream to forward chat request to {peer_username}")


        # Send response to originator
        resp_pdu = ChatInitRespPDU(status).pack() # [cite: 50]
        self.send_pdu(resp_pdu, stream_id)
        if status not in [SCP_CHAT_INIT_FORWARDED]: # if not successfully forwarded, originator stays IDLE
            self.client_state = SCPServerState.IDLE


    def handle_chat_fwd_resp(self, pdu, stream_id: int): # [cite: 56]
        originator_username = pdu.originator_username
        accepted = pdu.status_code == SCP_CHAT_FWD_ACCEPTED
        
        logging.info(f"User '{self.username}' responded to chat request from '{originator_username}': {'Accepted' if accepted else 'Rejected'}")

        originator_client = active_users.get(originator_username)

        if not originator_client or originator_client != self.pending_chat_request_from:
            logging.warning(f"CHAT_FWD_RESP from '{self.username}' for unknown/mismatched originator '{originator_username}'")
            # Error or ignore
            self.client_state = SCPServerState.IDLE # Reset self
            self.pending_chat_request_from = None
            return

        # Clear pending request state for target (self)
        self.pending_chat_request_from = None

        # Notify originator
        originator_stream_id = originator_client._quic.get_next_available_stream_id(is_unidirectional=False)
        if originator_stream_id is None:
             originator_stream_id = originator_client._quic.create_stream(is_unidirectional=False)

        if originator_stream_id is not None:
            if accepted:
                # Establish chat session
                self.current_chat_partner = originator_client
                originator_client.current_chat_partner = self
                
                self.client_state = SCPServerState.IN_CHAT # [cite: 76, 81]
                originator_client.client_state = SCPServerState.IN_CHAT # [cite: 76, 81]
                
                chat_sessions[self] = originator_client
                chat_sessions[originator_client] = self

                # Send CHAT_INIT_RESP (Accepted) to originator implicitly via peer state change
                # The spec CHAT_INIT_RESP covers FORWARDED or errors. Acceptance is via TEXT or specific notification.
                # For simplicity, we will consider the chat started.
                # Client will know from its AWAITING_PEER_RESPONSE to IN_CHAT transition triggered by server (e.g. dummy TEXT or new NOTIF)
                # Or send a specific notification. Let's assume no explicit message, client transitions on seeing TEXT.
                # The diagram for client (Figure 1 [cite: 69]) shows "recv ServNotif (Peer Acc)" to move to InChat.
                # Let's send a simple text message as a notification of acceptance for now, or a custom PDU if defined.
                # For now, let's send an empty text to signal chat start to originator.
                # Note: The spec [cite: 52] CHAT_INIT_RESP options: SCP_CHAT_INIT_FORWARDED, SCP_CHAT_INIT_ERR_PEER_NF etc.
                # It does not define a "chat accepted" status code for CHAT_INIT_RESP.
                # It shows peer sending FWD_RESP (acc/rej) to server, then server tells originator [cite: 69] "recv ServNotif (Peer Acc)".
                # We'll simulate this by sending a simple message or a new PDU type.
                # To stick to spec, we need a server notification. The client diagram has "recv ServNotif (Peer Acc)".
                # Let's make a simple text notification from server.
                start_msg_to_originator = TextPDU(f"Chat with {self.username} started.").pack()
                originator_client.send_pdu(start_msg_to_originator, originator_stream_id)
                
                start_msg_to_acceptor = TextPDU(f"Chat with {originator_username} started.").pack()
                self.send_pdu(start_msg_to_acceptor, stream_id) # Send to self (acceptor) on its stream

                logging.info(f"Chat started between '{self.username}' and '{originator_username}'. Both now IN_CHAT.")

            else: # Rejected
                resp_to_originator_pdu = ChatInitRespPDU(SCP_CHAT_INIT_ERR_PEER_REJECTED).pack() # [cite: 35]
                originator_client.send_pdu(resp_to_originator_pdu, originator_stream_id)
                originator_client.client_state = SCPServerState.IDLE # Originator goes back to IDLE
                self.client_state = SCPServerState.IDLE # Target (self) also goes back to IDLE
                logging.info(f"Chat rejected by '{self.username}'. Originator '{originator_username}' notified and both back to IDLE.")
        else:
            logging.error(f"Cannot find stream for originator {originator_username} to send chat FWD result.")
            # Originator might timeout. Target (self) goes back to IDLE.
            self.client_state = SCPServerState.IDLE
            if originator_client:
                originator_client.client_state = SCPServerState.IDLE # Try to reset originator state too.

        # Clear pending states
        if originator_client:
            originator_client.pending_chat_request_to = None
        self.pending_chat_request_from = None


    def handle_text(self, pdu, stream_id: int): # [cite: 22]
        message = pdu.text_message
        logging.info(f"User '{self.username}' sent TEXT: '{message}'")

        if self.current_chat_partner and self.current_chat_partner.client_state == SCPServerState.IN_CHAT:
            # Relay message to peer
            # Add sender's username to the message for clarity at receiver
            text_to_send = TextPDU(f"{self.username}: {message}").pack()
            
            partner_stream_id = self.current_chat_partner._quic.get_next_available_stream_id(is_unidirectional=False)
            if partner_stream_id is None:
                partner_stream_id = self.current_chat_partner._quic.create_stream(is_unidirectional=False)

            if partner_stream_id is not None:
                self.current_chat_partner.send_pdu(text_to_send, partner_stream_id)
                logging.info(f"Relayed TEXT from '{self.username}' to '{self.current_chat_partner.username}'")
            else:
                logging.warning(f"Could not find stream to relay TEXT to {self.current_chat_partner.username}")
                # Notify sender?
                err_pdu = ErrorPDU(SCP_ERR_INTERNAL_SERVER_ERR, "Could not send message to peer.").pack()
                self.send_pdu(err_pdu, stream_id)
        else:
            logging.warning(f"User '{self.username}' sent TEXT but not in a valid chat session.")
            err_pdu = ErrorPDU(SCP_ERR_UNEXPECTED_MSG_TYPE, "Cannot send TEXT, not in chat.").pack()
            self.send_pdu(err_pdu, stream_id)

    def handle_disconnect_req(self, stream_id: int): # [cite: 24]
        logging.info(f"DISCONNECT_REQ from '{self.username or self._get_client_key()}'")
        # ACK not implemented as per minimal requirement, but real protocol might ACK DISCONNECT_REQ
        self.cleanup_client(notify_peer=True)
        # No explicit response for DISCONNECT_REQ in PDU list, client will see connection terminate.


    def cleanup_client(self, notify_peer=False):
        client_key = self._get_client_key()
        if client_key and client_key in connected_clients:
            del connected_clients[client_key]
        
        if self.username and self.username in active_users:
            del active_users[self.username]
            logging.info(f"User '{self.username}' disconnected.")

        # If in chat, notify partner [cite: 7, 24, 78]
        if notify_peer and self.current_chat_partner:
            partner = self.current_chat_partner
            logging.info(f"Notifying '{partner.username}' about '{self.username}' disconnection.")
            
            notif_pdu = DisconnectNotifPDU(self.username).pack() # [cite: 34]
            partner_stream_id = partner._quic.get_next_available_stream_id(is_unidirectional=False)
            if partner_stream_id is None: partner_stream_id = partner._quic.create_stream(is_unidirectional=False)

            if partner_stream_id is not None:
                partner.send_pdu(notif_pdu, partner_stream_id)
            
            partner.current_chat_partner = None
            partner.client_state = SCPServerState.IDLE # [cite: 79]
            if partner in chat_sessions: del chat_sessions[partner]
            logging.info(f"'{partner.username}' moved to IDLE after peer disconnect.")

        if self in chat_sessions:
            del chat_sessions[self]
        
        self.current_chat_partner = None
        self.client_state = SCPServerState.IDLE # Or some terminal state
        self.username = None
        
        # For aioquic, closing the QuicConnectionProtocol instance.
        # This might be handled by ConnectionTerminated event too.
        self.close() # Close the QUIC connection


async def main(host="0.0.0.0", port=SERVER_PORT, certificate="cert.pem", private_key="privkey.pem"):
    configuration = QuicConfiguration(
        alpn_protocols=["scp-v1"], # Application-Layer Protocol Negotiation
        is_client=False,
        max_datagram_frame_size=65536,
    )
    configuration.load_cert_chain(certificate, private_key)

    logging.info(f"Starting SCP Server on {host}:{port}")
    await serve(
        host,
        port,
        configuration=configuration,
        create_protocol=SCPServerProtocol,
    )
    try:
        await asyncio.Event().wait() # Keep server running indefinitely
    except KeyboardInterrupt:
        logging.info("Server shutting down...")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - SERVER - %(levelname)s - %(message)s",
    )
    # Check for certificate files
    if not (os.path.exists("cert.pem") and os.path.exists("privkey.pem")):
        logging.error("Certificate (cert.pem) or private key (privkey.pem) not found.")
        logging.error("Please generate them using: openssl req -x509 -newkey rsa:2048 -keyout privkey.pem -out cert.pem -days 365 -nodes")
    else:
        asyncio.run(main())
