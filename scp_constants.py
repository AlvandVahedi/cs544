import enum

# Protocol Version
SCP_VERSION_1_0 = 0x01 # [cite: 33]

# Message Types Enum [cite: 33, 34]
class SCPMessageType(enum.IntEnum):
    CONNECT_REQ = 0x01
    CONNECT_RESP = 0x02
    CHAT_INIT_REQ = 0x03
    CHAT_INIT_RESP = 0x04
    CHAT_FWD_REQ = 0x05
    CHAT_FWD_RESP = 0x06
    TEXT = 0x07
    DISCONNECT_REQ = 0x08
    DISCONNECT_NOTIF = 0x09
    ACK = 0x0A  # General acknowledgment
    ERROR = 0x0B # Error notification

# Status Codes for SCP_MSG_CONNECT_RESP [cite: 35]
SCP_CONNECT_SUCCESS = 0x00
SCP_CONNECT_ERR_USER_EXISTS = 0x01
SCP_CONNECT_ERR_AUTH_FAILED = 0x02 # e.g. unknown user
SCP_CONNECT_ERR_SERVER_FULL = 0x03
SCP_CONNECT_ERR_VERSION_MISMATCH = 0x04

# Status Codes for SCP_MSG_CHAT_INIT_RESP [cite: 35]
SCP_CHAT_INIT_FORWARDED = 0x00
SCP_CHAT_INIT_ERR_PEER_NF = 0x01 # Peer Not Found
SCP_CHAT_INIT_ERR_PEER_BUSY = 0x02
SCP_CHAT_INIT_ERR_SELF_CHAT = 0x03
SCP_CHAT_INIT_ERR_PEER_REJECTED = 0x04

# Status Codes for SCP_MSG_CHAT_FWD_RESP [cite: 35]
SCP_CHAT_FWD_ACCEPTED = 0x00
SCP_CHAT_FWD_REJECTED = 0x01

# Status Codes for SCP_MSG_ACK [cite: 35]
SCP_ACK_SUCCESS = 0x00
SCP_ACK_FAILURE = 0x01

# General Error Codes for SCP_MSG_ERROR [cite: 36]
SCP_ERR_MALFORMED_MSG = 0x0001
SCP_ERR_UNEXPECTED_MSG_TYPE = 0x0002
SCP_ERR_INVALID_PAYLOAD_LEN = 0x0003
SCP_ERR_INTERNAL_SERVER_ERR = 0x0004
SCP_ERR_UNSUPPORTED_VERSION = 0x0005

# Client Protocol States Enum [cite: 36]
class SCPClientState(enum.Enum):
    DISCONNECTED = 0
    CONNECTING = 1
    IDLE = 2 # Connected to server, not in chat
    INITIATING_CHAT = 3 # Sent CHAT_INIT_REQ, awaiting server response
    PENDING_PEER_ACCEPT = 4 # Received CHAT_FWD_REQ, must respond
    AWAITING_PEER_RESPONSE = 5 # After CHAT_INIT_REQ was forwarded, waiting for peer's decision
    IN_CHAT = 6 # Chat session active
    DISCONNECTING = 7 # Sent DISCONNECT_REQ

# Server Protocol States Enum (per client connection) [cite: 38]
class SCPServerState(enum.Enum):
    AUTHENTICATING = 0 # Received CONNECT_REQ, validating
    IDLE = 1 # Client authenticated, not in chat
    AWAITING_PEER_FOR_INIT = 2 # Client A requested chat with B, server sent FWD_REQ to B (state for A)
    AWAITING_CHAT_RESPONSE = 3 # Client B received FWD_REQ from A, server awaits B's FWD_RESP (state for B)
    IN_CHAT = 4 # Client is in an active chat
    RELAYING_DISCONNECT = 5 # Client is disconnecting, need to notify peer if in chat

# Hardcoded server port [cite: 150]
SERVER_PORT = 4433
SERVER_HOST = 'localhost' # Default server host for client

# Header format: version (1B), type (1B), payload_length (2B, Big Endian)
HEADER_FORMAT = '!BBH'
HEADER_SIZE = 4 # Bytes
