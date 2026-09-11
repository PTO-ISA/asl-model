"""Minimal dependency-free ELF64 loader for the new ASL runtime."""

from __future__ import annotations

import struct
from pathlib import Path

from .protocol import ElfLoadRequest, ProgramImage, ProgramSegment


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
SYMBOL = struct.Struct("<IBBHQQ")
PT_LOAD = 1
PF_X = 1
PF_W = 2
PF_R = 4


class ElfFormatError(ValueError):
    """Raised when an ELF cannot be represented by the neutral image model."""


class ElfLoader:
    """Load PT_LOAD segments without exposing libelf to the runtime."""

    def load(self, request: ElfLoadRequest) -> ProgramImage:
        if request.path is None:
            raise ElfFormatError("the demo ELF loader requires a filesystem path")
        path = Path(request.path)
        data = path.read_bytes()
        if len(data) < ELF_HEADER.size:
            raise ElfFormatError(f"ELF is truncated: {path}")
        fields = ELF_HEADER.unpack_from(data)
        ident, elf_type, machine, _version, entry, phoff, shoff, _flags, ehsize, phentsize, phnum, shentsize, shnum, _shstrndx = fields
        if ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1:
            raise ElfFormatError("only little-endian ELF64 files are supported")
        if request.expected_machine is not None and machine != request.expected_machine:
            raise ElfFormatError(
                f"ELF machine mismatch: expected 0x{request.expected_machine:x}, "
                f"found 0x{machine:x}"
            )
        if ehsize != ELF_HEADER.size or phentsize != PROGRAM_HEADER.size:
            raise ElfFormatError("unsupported ELF header/program-header size")
        segments: list[ProgramSegment] = []
        for index in range(phnum):
            offset = phoff + index * phentsize
            if offset + phentsize > len(data):
                raise ElfFormatError("ELF program header table is truncated")
            p_type, flags, file_offset, address, _paddr, file_size, memory_size, alignment = PROGRAM_HEADER.unpack_from(data, offset)
            if p_type != PT_LOAD:
                continue
            if file_size > memory_size or file_offset + file_size > len(data):
                raise ElfFormatError(f"invalid PT_LOAD segment {index}")
            permissions = "".join(flag for flag, bit in (("r", PF_R), ("w", PF_W), ("x", PF_X)) if flags & bit)
            segments.append(
                ProgramSegment(
                    address=address,
                    data=data[file_offset : file_offset + file_size],
                    memory_size=memory_size,
                    permissions=permissions or "r",
                    alignment=alignment if alignment else 1,
                    name=f"PT_LOAD[{index}]",
                )
            )
        if not segments:
            raise ElfFormatError("ELF contains no PT_LOAD segments")
        symbols = self._symbols(data, shoff, shentsize, shnum)
        relocation_delta = 0
        if request.requested_base is not None:
            image_base = min(segment.address for segment in segments)
            relocation_delta = request.requested_base - image_base
            segments = [
                ProgramSegment(
                    address=segment.address + relocation_delta,
                    data=segment.data,
                    memory_size=segment.memory_size,
                    permissions=segment.permissions,
                    alignment=segment.alignment,
                    name=segment.name,
                )
                for segment in segments
            ]
            entry += relocation_delta
            symbols = {name: value + relocation_delta for name, value in symbols.items()}
        return ProgramImage(
            entry_point=entry,
            segments=tuple(segments),
            symbols=symbols,
            metadata={
                "elf_type": elf_type,
                "machine": machine,
                "path": str(path),
                "relocation_delta": relocation_delta,
                "pto_isa_note_status": (
                    "present-unverified" if b".note.pto.isa" in data
                    else "not-provided"
                ),
            },
            format="elf64",
        )

    @staticmethod
    def _symbols(data: bytes, shoff: int, shentsize: int, shnum: int) -> dict[str, int]:
        if shoff == 0 or shentsize != SECTION_HEADER.size:
            return {}
        sections = []
        for index in range(shnum):
            offset = shoff + index * shentsize
            if offset + shentsize > len(data):
                return {}
            sections.append(SECTION_HEADER.unpack_from(data, offset))
        symbols: dict[str, int] = {}
        for section in sections:
            _name, sh_type, _flags, _addr, offset, size, link, _info, _align, entsize = section
            if sh_type not in {2, 11} or entsize != SYMBOL.size or link >= len(sections):
                continue
            str_offset = sections[link][4]
            str_size = sections[link][5]
            strings = data[str_offset : str_offset + str_size]
            for position in range(0, size, entsize):
                if offset + position + entsize > len(data):
                    break
                name, _info, _other, _shndx, value, _size = SYMBOL.unpack_from(data, offset + position)
                if name == 0 or value == 0 or name >= len(strings):
                    continue
                end = strings.find(b"\0", name)
                if end > name:
                    symbols.setdefault(strings[name:end].decode("utf-8", "replace"), value)
        return symbols
