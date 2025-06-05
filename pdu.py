import struct
from scp_constants import SCP_VERSION_1_0, HEADER_FORMAT, HEADER_SIZE, SCPMessageType

# Base PDU class could be made more abstract if needed,
# but for this direct implementation, functions are straightforward.

class SCPHeader:
    def __init__(self, message_type: SCPMessageType, payload_length: int):
        self.protocol_version = SCP_VERSION_1_0
        self.message_type = message_type
        self.payload_length = payload_length

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

# PDU Specific Structures

class ConnectReqPDU:
    MESSAGE_TYPE = SCPMessageType.CONNECT_REQ

    def __init__(self, username: str):
        self.username = username

    def pack(self) -> bytes:
        username_bytes = self.username.encode('utf-8')
        username_len = len(username_bytes)
        payload = struct.pack(f'!B{username_len}s', username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ConnectReqPDU':
        username_len = payload[0]
        username = payload[1:1+username_len].decode('utf-8')
        return ConnectReqPDU(username)

class ConnectRespPDU:
    MESSAGE_TYPE = SCPMessageType.CONNECT_RESP

    def __init__(self, status_code: int):
        self.status_code = status_code

    def pack(self) -> bytes:
        payload = struct.pack('!B', self.status_code)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ConnectRespPDU':
        status_code = struct.unpack('!B', payload)[0]
        return ConnectRespPDU(status_code)

class ChatInitReqPDU:
    MESSAGE_TYPE = SCPMessageType.CHAT_INIT_REQ

    def __init__(self, peer_username: str):
        self.peer_username = peer_username

    def pack(self) -> bytes:
        peer_username_bytes = self.peer_username.encode('utf-8')
        peer_username_len = len(peer_username_bytes)
        payload = struct.pack(f'!B{peer_username_len}s', peer_username_len, peer_username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatInitReqPDU':
        peer_username_len = payload[0]
        peer_username = payload[1:1+peer_username_len].decode('utf-8')
        return ChatInitReqPDU(peer_username)

class ChatInitRespPDU:
    MESSAGE_TYPE = SCPMessageType.CHAT_INIT_RESP

    def __init__(self, status_code: int):
        self.status_code = status_code

    def pack(self) -> bytes:
        payload = struct.pack('!B', self.status_code)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatInitRespPDU':
        status_code = struct.unpack('!B', payload)[0]
        return ChatInitRespPDU(status_code)

class ChatFwdReqPDU:
    MESSAGE_TYPE = SCPMessageType.CHAT_FWD_REQ

    def __init__(self, originator_username: str):
        self.originator_username = originator_username

    def pack(self) -> bytes:
        username_bytes = self.originator_username.encode('utf-8')
        username_len = len(username_bytes)
        payload = struct.pack(f'!B{username_len}s', username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatFwdReqPDU':
        username_len = payload[0]
        originator_username = payload[1:1+username_len].decode('utf-8')
        return ChatFwdReqPDU(originator_username)

class ChatFwdRespPDU:
    MESSAGE_TYPE = SCPMessageType.CHAT_FWD_RESP

    def __init__(self, status_code: int, originator_username: str):
        self.status_code = status_code
        self.originator_username = originator_username

    def pack(self) -> bytes:
        username_bytes = self.originator_username.encode('utf-8')
        username_len = len(username_bytes)
        payload = struct.pack(f'!BB{username_len}s', self.status_code, username_len, username_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'ChatFwdRespPDU':
        status_code = payload[0]
        username_len = payload[1]
        originator_username = payload[2:2+username_len].decode('utf-8')
        return ChatFwdRespPDU(status_code, originator_username)

class TextPDU:
    MESSAGE_TYPE = SCPMessageType.TEXT

    def __init__(self, text_message: str):
        self.text_message = text_message

    def pack(self) -> bytes:
        message_bytes = self.text_message.encode('utf-8')
        message_len = len(message_bytes)
        # Note: SCP doc specifies uint16_t for text_len
        payload = struct.pack(f'!H{message_len}s', message_len, message_bytes)
        header = SCPHeader(self.MESSAGE_TYPE, len(payload))
        return header.pack() + payload

    @staticmethod
    def unpack(payload: bytes) -> 'TextPDU':
        text_len = struct.unpack('!H', payload[:2])[0]
        text_message = payload[2:2+text_len].decode('utf-8')
        return TextPDU(text_message)

class DisconnectReqPDU:
    MESSAGE_TYPE = SCPMessageType.DISCONNECT_REQ

    def __init__(self):
        pass # No payload

    def pack(self) -> bytes:
        header = SCPHeader(self.MESSAGE_TYPE, 0) # payload_length is 0
        return header.pack()

    @staticmethod
    def unpack(payload: bytes) -> 'DisconnectReqPDU':
        # No payload to unpack
        return DisconnectReqPDU()

class DisconnectNotifPDU:
    MESSAGE_TYPE = SCPMessageType.DISCONNECT_NOTIF

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

class ErrorPDU:
    MESSAGE_TYPE = SCPMessageType.ERROR

    def __init__(self, error_code: int, error_message: str = ""):
        self.error_code = error_code
        self.error_message = error_message

    def pack(self) -> bytes:
        message_bytes = self.error_message.encode('utf-8')
        message_len = len(message_bytes)
        # uint16_t error_code; uint8_t error_message_len; char error_message[]
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
