"""Parse and validate the canonical PTO ISA ELF compatibility note."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import struct

from .closure_artifacts import canonical_json_bytes, validate_identity


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
ELF_MAGIC = b"\x7fELF"
SHT_NOTE = 7
SHT_STRTAB = 3
SHF_ALLOC = 2
PTO_NOTE_TYPE = 1
PTO_NOTE_OWNER = b"PTO\0"


class PTOISANoteError(ValueError):
    """The ELF PTO compatibility note violates its wire contract."""


@dataclasses.dataclass(frozen=True)
class PTOISANote:
    release: str
    encoding_abi: str
    encoding_projection_sha256: str
    descriptor_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "release": self.release,
            "encoding_abi": self.encoding_abi,
            "encoding_projection_sha256": self.encoding_projection_sha256,
        }


def _section_name(strings: bytes, offset: int) -> str:
    if offset >= len(strings):
        raise PTOISANoteError("section name is outside the section string table")
    end = strings.find(b"\0", offset)
    if end < 0:
        raise PTOISANoteError("unterminated section name")
    try:
        return strings[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PTOISANoteError("section name is not UTF-8") from error


def parse_pto_isa_note(path: pathlib.Path) -> PTOISANote:
    """Require exactly one canonical allocatable ``.note.pto.isa`` record."""
    content = path.read_bytes()
    if len(content) < ELF_HEADER.size:
        raise PTOISANoteError("ELF header is truncated")
    fields = ELF_HEADER.unpack_from(content)
    identification = fields[0]
    if identification[:4] != ELF_MAGIC:
        raise PTOISANoteError("ELF magic mismatch")
    if identification[4:7] != b"\x02\x01\x01":
        raise PTOISANoteError("PTO identity parser requires ELF64 little-endian version 1")
    section_offset = fields[6]
    section_entry_size = fields[11]
    section_count = fields[12]
    string_index = fields[13]
    if not section_count or section_entry_size != SECTION_HEADER.size:
        raise PTOISANoteError("ELF section table is required")
    table_size = section_entry_size * section_count
    if section_offset > len(content) or table_size > len(content) - section_offset:
        raise PTOISANoteError("section-header table is truncated")
    if string_index >= section_count:
        raise PTOISANoteError("invalid section-name string-table index")
    sections = [
        SECTION_HEADER.unpack_from(content, section_offset + index * section_entry_size)
        for index in range(section_count)
    ]
    string_section = sections[string_index]
    if string_section[1] != SHT_STRTAB:
        raise PTOISANoteError("section-name table is not SHT_STRTAB")
    string_offset, string_size = string_section[4], string_section[5]
    if string_offset > len(content) or string_size > len(content) - string_offset:
        raise PTOISANoteError("section-name string table is truncated")
    strings = content[string_offset:string_offset + string_size]
    matches = [
        section for section in sections
        if _section_name(strings, section[0]) == ".note.pto.isa"
    ]
    if len(matches) != 1:
        raise PTOISANoteError("ELF must contain exactly one .note.pto.isa section")
    section = matches[0]
    if section[1] != SHT_NOTE:
        raise PTOISANoteError(".note.pto.isa section type must be SHT_NOTE")
    if section[2] != SHF_ALLOC:
        raise PTOISANoteError(".note.pto.isa flags must be exactly SHF_ALLOC")
    if section[8] != 4:
        raise PTOISANoteError(".note.pto.isa alignment must be 4")
    offset, size = section[4], section[5]
    if offset % 4:
        raise PTOISANoteError(".note.pto.isa file offset must be 4-byte aligned")
    if offset > len(content) or size > len(content) - offset:
        raise PTOISANoteError(".note.pto.isa payload is truncated")
    data = content[offset:offset + size]
    if len(data) < 16:
        raise PTOISANoteError(".note.pto.isa record is truncated")
    namesz, descsz, note_type = struct.unpack_from("<III", data)
    expected_size = (16 + descsz + 3) & ~3
    if namesz != 4 or note_type != PTO_NOTE_TYPE or len(data) != expected_size:
        raise PTOISANoteError(".note.pto.isa must contain one canonical type-1 record")
    if data[12:16] != PTO_NOTE_OWNER:
        raise PTOISANoteError(".note.pto.isa owner must be PTO\\0")
    descriptor = data[16:16 + descsz]
    if any(data[16 + descsz:]):
        raise PTOISANoteError(".note.pto.isa padding must be zero")
    try:
        value = json.loads(descriptor.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PTOISANoteError(".note.pto.isa descriptor is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise PTOISANoteError(".note.pto.isa descriptor must be an object")
    if descriptor != canonical_json_bytes(value):
        raise PTOISANoteError(".note.pto.isa descriptor is not canonical compact JSON")
    try:
        validate_identity({
            "release": value.get("release"),
            "publication_version": "0.58.5.1",
            "encoding_abi": value.get("encoding_abi"),
            "encoding_projection_sha256": value.get("encoding_projection_sha256"),
        }, ".note.pto.isa")
    except ValueError as error:
        raise PTOISANoteError(str(error)) from error
    if set(value) != {"release", "encoding_abi", "encoding_projection_sha256"}:
        raise PTOISANoteError(".note.pto.isa descriptor fields mismatch")
    return PTOISANote(
        release=value["release"],
        encoding_abi=value["encoding_abi"],
        encoding_projection_sha256=value["encoding_projection_sha256"],
        descriptor_sha256=hashlib.sha256(descriptor).hexdigest(),
    )


__all__ = ["PTOISANote", "PTOISANoteError", "parse_pto_isa_note"]
