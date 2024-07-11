import struct
from enum import IntEnum

import base58
from construct import Struct as cStruct  # type: ignore
from solders.pubkey import Pubkey
from spl.token._layouts import MINT_LAYOUT

from celeritas.constants import aclient

MAX_NAME_LENGTH = 32
MAX_SYMBOL_LENGTH = 10
MAX_URI_LENGTH = 200
MAX_CREATOR_LENGTH = 34
MAX_CREATOR_LIMIT = 5


class InstructionType(IntEnum):
    CREATE_METADATA = 0
    UPDATE_METADATA = 1


METADATA_PROGRAM_ID = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")
SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
SYSVAR_RENT_PUBKEY = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
ASSOCIATED_TOKEN_ACCOUNT_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")


def get_metadata_account(mint_key):
    return Pubkey.find_program_address(
        [b"metadata", bytes(METADATA_PROGRAM_ID), bytes(Pubkey.from_string(mint_key))],
        METADATA_PROGRAM_ID,
    )[0]


def unpack_metadata_account(data):
    assert data[0] == 4
    i = 1
    source_account = base58.b58encode(bytes(struct.unpack("<" + "B" * 32, data[i : i + 32])))
    i += 32
    mint_account = base58.b58encode(bytes(struct.unpack("<" + "B" * 32, data[i : i + 32])))
    i += 32
    name_len = struct.unpack("<I", data[i : i + 4])[0]
    i += 4
    name = struct.unpack("<" + "B" * name_len, data[i : i + name_len])
    i += name_len
    symbol_len = struct.unpack("<I", data[i : i + 4])[0]
    i += 4
    symbol = struct.unpack("<" + "B" * symbol_len, data[i : i + symbol_len])
    i += symbol_len
    uri_len = struct.unpack("<I", data[i : i + 4])[0]
    i += 4
    uri = struct.unpack("<" + "B" * uri_len, data[i : i + uri_len])
    i += uri_len
    fee = struct.unpack("<h", data[i : i + 2])[0]
    i += 2
    has_creator = data[i]
    i += 1
    creators = []
    verified = []
    share = []
    if has_creator:
        creator_len = struct.unpack("<I", data[i : i + 4])[0]
        i += 4
        for _ in range(creator_len):
            creator = base58.b58encode(bytes(struct.unpack("<" + "B" * 32, data[i : i + 32])))
            creators.append(creator)
            i += 32
            verified.append(data[i])
            i += 1
            share.append(data[i])
            i += 1
    primary_sale_happened = bool(data[i])
    i += 1
    is_mutable = bool(data[i])
    metadata = {
        "update_authority": source_account.decode(),
        "mint": mint_account.decode(),
        "data": {
            "name": str(bytes(name).decode("utf-8").strip("\x00")),
            "symbol": str(bytes(symbol).decode("utf-8").strip("\x00")),
            "uri": str(bytes(uri).decode("utf-8").strip("\x00")),
            "seller_fee_basis_points": fee,
            "creators": creators,
            "verified": verified,
            "share": share,
        },
        "primary_sale_happened": primary_sale_happened,
        "is_mutable": is_mutable,
    }
    return metadata


async def get_metadata(mint_key):
    metadata_account = get_metadata_account(mint_key)
    data = (await aclient.get_account_info(metadata_account)).value.data
    info = MINT_LAYOUT.parse((await aclient.get_account_info(Pubkey.from_string(mint_key))).value.data)
    metadata = unpack_metadata_account(data)
    metadata["decimals"] = info.decimals
    metadata["supply"] = info.supply
    return metadata


async def get_token_supply(mint_key):
    info = MINT_LAYOUT.parse((await aclient.get_account_info(Pubkey.from_string(mint_key))).value.data)
    return info.supply
