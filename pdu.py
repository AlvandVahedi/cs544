import struct
from scp_constants import SCP_VERSION_1_0, HEADER_FORMAT, HEADER_SIZE, SCPMessageType

# Base PDU class could be made more abstract if needed,
# but for this direct implementation, functions are straightforward.

class SCPHeader:
    def __init__(self, message_type: SCPMessageType, payload_length: int):
        self.protocol_version = SCP_VERSION_1_0 # [cite: 33]
        self.message_type = message_type # [cite: 40]
        self.payload_length = payload_length # [cite: 40]

    def pack(self) -> bytes:
        return struct.pack(HEADER_FORMAT, self.protocol_version, self.message_type.value, self.payload_length)

    @staticmethod
    def unpack(data: bytes) -> 'SCPHeader':
        if len(data) < HEADER_SIZE:
            raise ValueError("Data too short for SCP Header")
        version, msg_type_val, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        if version != SCP_VERSION_1_0:
            # Or handle as SCP_ERR_UNSUPPORTED_VERSION by the caller
            raise ValueError(f"Unsupported SCP version: {version}")
        return SCPHeader(SCPMessageType(msg_type_val), payload_len)

# PDU Specific Structures [cite: 41, 43, 48, 50, 54, 56, 59, 61]

class ConnectReqPDU: # [cite: 41]
    MESSAGE_TYPE = SCPMessageType.CONNECT_REQ

    def __init__(self, username: str):
        self.username = username # [cite: 42]

    def pack(self) -> bytes:
        username_bytes = self.username.encode('utf-8') # [cite: 31]
        username_len = len(username_bytes) # [cite: 42]
        payload = struct.pack(f'!B{username_len}s', username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ConnectReqPDU':
        username_len = payload[0]
        username = payload[1:1+username_len].decode('utf-8') # [cite: 42]
        return ConnectReqPDU(username)

class ConnectRespPDU: # [cite: 43]
    MESSAGE_TYPE = SCPMessageType.CONNECT_RESP

    def __init__(self, status_code: int):
        self.status_code = status_code # [cite: 44]

    def pack(self) -> bytes:
        payload = struct.pack('!B', self.status_code) # [cite: 47]
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ConnectRespPDU':
        status_code = struct.unpack('!B', payload)[0] # [cite: 47]
        return ConnectRespPDU(status_code)

class ChatInitReqPDU: # [cite: 48]
    MESSAGE_TYPE = SCPMessageType.CHAT_INIT_REQ

    def __init__(self, peer_username: str):
        self.peer_username = peer_username # [cite: 49]

    def pack(self) -> bytes:
        peer_username_bytes = self.peer_username.encode('utf-8')
        peer_username_len = len(peer_username_bytes) # [cite: 49]
        payload = struct.pack(f'!B{peer_username_len}s', peer_username_len, peer_username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatInitReqPDU':
        peer_username_len = payload[0]
        peer_username = payload[1:1+peer_username_len].decode('utf-8') # [cite: 49]
        return ChatInitReqPDU(peer_username)

class ChatInitRespPDU: # [cite: 50]
    MESSAGE_TYPE = SCPMessageType.CHAT_INIT_RESP

    def __init__(self, status_code: int):
        self.status_code = status_code # [cite: 51]

    def pack(self) -> bytes:
        payload = struct.pack('!B', self.status_code) # [cite: 53]
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatInitRespPDU':
        status_code = struct.unpack('!B', payload)[0] # [cite: 53]
        return ChatInitRespPDU(status_code)

class ChatFwdReqPDU: # [cite: 54]
    MESSAGE_TYPE = SCPMessageType.CHAT_FWD_REQ

    def __init__(self, originator_username: str):
        self.originator_username = originator_username # [cite: 55]

    def pack(self) -> bytes:
        username_bytes = self.originator_username.encode('utf-8')
        username_len = len(username_bytes) # [cite: 55]
        payload = struct.pack(f'!B{username_len}s', username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatFwdReqPDU':
        username_len = payload[0]
        originator_username = payload[1:1+username_len].decode('utf-8') # [cite: 55]
        return ChatFwdReqPDU(originator_username)

class ChatFwdRespPDU: # [cite: 56]
    MESSAGE_TYPE = SCPMessageType.CHAT_FWD_RESP

    def __init__(self, status_code: int, originator_username: str):
        self.status_code = status_code # [cite: 57]
        self.originator_username = originator_username # [cite: 57, 58]

    def pack(self) -> bytes:
        username_bytes = self.originator_username.encode('utf-8')
        username_len = len(username_bytes) # [cite: 57]
        payload = struct.pack(f'!BB{username_len}s', self.status_code, username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatFwdRespPDU':
        status_code = payload[0]
        username_len = payload[1]
        originator_username = payload[2:2+username_len].decode('utf-8') # [cite: 57]
        return ChatFwdRespPDU(status_code, originator_username)

class TextPDU: # [cite: 59]
    MESSAGE_TYPE = SCPMessageType.TEXT

    def __init__(self, text_message: str):
        self.text_message = text_message # [cite: 60]

    def pack(self) -> bytes:
        message_bytes = self.text_message.encode('utf-8')
        message_len = len(message_bytes) # [cite: 60]
        # Note: SCP doc specifies uint16_t for text_len [cite: 60]
        payload = struct.pack(f'!H{message_len}s', message_len, message_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'TextPDU':
        text_len = struct.unpack('!H', payload[:2])[0] # [cite: 60]
        text_message = payload[2:2+text_len].decode('utf-8') # [cite: 60]
        return TextPDU(text_message)

class DisconnectReqPDU: # [cite: 61]
    MESSAGE_TYPE = SCPMessageType.DISCONNECT_REQ

    def __init__(self):
        pass # No payload [cite: 62]

    def pack(self) -> bytes:
        header = SCPHeader(self.MESSAGE_TYPE, 0) # payload_length is 0 [cite: 62]
        return header.pack()

    @staticmethod
    def unpack(payload: bytes) -> 'DisconnectReqPDU':
        # No payload to unpack
        return DisconnectReqPDU()

class DisconnectNotifPDU:
    MESSAGE_TYPE = SCPMessageType.DISCONNECT_NOTIF # [cite: 34]

    def __init__(self, peer_username: str):
        self.peer_username = peer_username

    def pack(self) -> bytes:
        username_bytes = self.peer_username.encode('utf-8')
        username_len = len(username_bytes)
        payload = struct.pack(f'!B{username_len}s', username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'DisconnectNotifPDU':
        username_len = payload[0]
        peer_username = payload[1:1+username_len].decode('utf-8')
        return DisconnectNotifPDU(peer_username)

class ErrorPDU: # Based on structure from [cite: 63] (Listing 13)
    MESSAGE_TYPE = SCPMessageType.ERROR

    def __init__(self, error_code: int, error_message: str = ""):
        self.error_code = error_code # [cite: 63]
        self.error_message = error_message # [cite: 63]

    def pack(self) -> bytes:
        message_bytes = self.error_message.encode('utf-8')
        message_len = len(message_bytes)
        # uint16_t error_code; uint8_t error_message_len; char error_message[]; [cite: 63]
        payload = struct.pack(f'!HB{message_len}s', self.error_code, message_len, message_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ErrorPDU':
        error_code = struct.unpack('!H', payload[:2])[0]
        message_len = payload[2]
        error_message = payload[3:3+message_len].decode('utf-8')
        return ErrorPDU(error_code, error_message)

# Mapping message types to their unpack methods for easier dispatch
PDU_UNPACKERS = {
    SCPMessageType.CONNECT_REQ: ConnectReqPDU.unpack,
    SCPMessageType.CONNECT_RESP: ConnectRespPDU.unpack,
    SCPMessageType.CHAT_INIT_REQ: ChatInitReqPDU.unpack,
    SCPMessageType.CHAT_INIT_RESP: ChatInitRespPDU.unpack,
    SCPMessageType.CHAT_FWD_REQ: ChatFwdReqPDU.unpack,
    SCPMessageType.CHAT_FWD_RESP: ChatFwdRespPDU.unpack,
    SCPMessageType.TEXT: TextPDU.unpack,
    SCPMessageType.DISCONNECT_REQ: DisconnectReqPDU.unpack,
    SCPMessageType.DISCONNECT_NOTIF: DisconnectNotifPDU.unpack,
    SCPMessageType.ERROR: ErrorPDU.unpack,
    # ACK PDU is not fully defined in usage for this minimal implementation yet
}
