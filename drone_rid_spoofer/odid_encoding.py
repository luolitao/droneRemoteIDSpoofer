"""Backward-compatibility re-export module for odid_encoding.

All encoding/decoding logic has moved to drone_rid_spoofer.messages.
This module exists so existing imports (sniff_gb.py, verify_*.py, diagnose_gb.py)
continue to work without changes.
"""

from drone_rid_spoofer.messages import (  # noqa: F401
    MESSAGE_SIZE as ODID_MESSAGE_SIZE,
    MsgType,
    encode_basic_id,
    encode_location,
    encode_self_id,
    build_gb_pack as build_message_pack,
    decode_message_pack,
    # Constants re-exported for backward compat
    IDTYPE_SERIAL_NUMBER as ODID_IDTYPE_SERIAL_NUMBER,
    IDTYPE_CAA_REGISTRATION as ODID_IDTYPE_CAA_REGISTRATION,
    IDTYPE_UTM_ASSIGNED as ODID_IDTYPE_UTM_ASSIGNED,
    IDTYPE_SPECIFIC_SESSION as ODID_IDTYPE_SPECIFIC_SESSION,
    UATYPE_HELICOPTER as ODID_UATYPE_HELICOPTER,
    STATUS_UNDECLARED as ODID_STATUS_UNDECLARED,
)

ODID_PROTO_VERSION = 1
ODID_MSGTYPE_BASIC_ID = 0x0
ODID_MSGTYPE_LOCATION = 0x1
ODID_MSGTYPE_AUTH = 0x2
ODID_MSGTYPE_SELF_ID = 0x3
ODID_MSGTYPE_SYSTEM = 0x4
ODID_MSGTYPE_OPERATOR_ID = 0x5
ODID_MESSAGETYPE_PACKED = 0x0F
