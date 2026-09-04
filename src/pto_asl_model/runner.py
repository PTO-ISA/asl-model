"""Run one static PTO ELF in one pinned ASLRef process."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence

from .closure_artifacts import (
    ENCODING_ABI,
    ENCODING_PROJECTION_SHA256,
    PUBLICATION_VERSION,
    RELEASE,
    canonical_repository_url,
)
from .elf_note import PTOISANote, parse_pto_isa_note


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
SYMBOL_ENTRY = struct.Struct("<IBBHQQ")
ELF_MAGIC = b"\x7fELF"
ELF_CLASS_64 = 2
ELF_DATA_LITTLE = 1
ELF_VERSION_CURRENT = 1
ELF_TYPE_EXEC = 2
PTO_ELF_MACHINE = 0xE9
PTO_HOSTED_SP_GPR = 1
PTO_HOSTED_RA_GPR = 10
PROGRAM_TYPE_LOAD = 1
PROGRAM_FLAG_EXECUTE = 1
PROGRAM_FLAG_WRITE = 2
PROGRAM_FLAG_READ = 4
SECTION_TYPE_SYMBOLS = {2, 11}
SECTION_INDEX_ABSOLUTE = 0xFFF1
REFERENCE_MEMORY_BYTES = 65536
REFERENCE_TILE_ELEMENTS = 32768
MAX_HOSTED_MEMORY_BYTES = 16 * 1024 * 1024
MEMORY_CONFIG_RE = re.compile(
    rb"config PTO_MODEL_MEMORY_BYTES : integer \{256\.\.[0-9]+\} = [0-9]+;"
)
TILE_CONFIG_RE = re.compile(
    rb"config PTO_MODEL_TILE_ELEMENTS : integer \{1\.\.[0-9]+\} = [0-9]+;"
)
MEMORY_RESET_LOOP_RE = re.compile(
    rb"    for index = 0 to PTO_MODEL_MEMORY_BYTES - 1 do\n"
    rb"        _Memory\[\[index\]\] = Zeros\{8\};\n"
    rb"    end;"
)
RESULT_RE = re.compile(r"^PTO_RESULT_BYTE\s+(\d+)\s+(\d+)\s*$")
FINAL_TPC_RE = re.compile(r"^PTO_FINAL_TPC\s+(\d+)\s*$")
SIDECAR_SCHEMA = "pto-asl-elf-sidecar-v1"
HOST_MEMORY_RUNNER_SCHEMA = "pto-aslref-host-memory-runner-v1"
HOST_MEMORY_RUNNER_SOURCE = pathlib.Path(__file__).with_name(
    "aslref_host_memory.ml"
)


class ElfError(ValueError):
    """ELF input violates the hosted runner contract."""


@dataclasses.dataclass(frozen=True)
class LoadSegment:
    address: int
    data: bytes
    memory_size: int
    flags: int
    alignment: int

    @property
    def end(self) -> int:
        return self.address + self.memory_size


@dataclasses.dataclass(frozen=True)
class ElfImage:
    entry: int
    segments: tuple[LoadSegment, ...]
    symbols: tuple["ElfSymbol", ...]
    sha256: str
    pto_isa_note: PTOISANote | None = None


@dataclasses.dataclass(frozen=True)
class ElfSymbol:
    name: str
    value: int
    size: int
    section_index: int


@dataclasses.dataclass(frozen=True)
class RunConfiguration:
    asl_spec: pathlib.Path
    aslref: pathlib.Path
    elf: pathlib.Path
    stop_pc: int
    max_steps: int
    result_address: int
    result_size: int
    memory_bytes: int = REFERENCE_MEMORY_BYTES
    tile_elements: int = REFERENCE_TILE_ELEMENTS
    runtime_typecheck: str = "strict"
    stop_after_hits: int = 1
    start_pc: int = 0
    start_acr: int = 0
    return_pc: int = 0
    stack_top: int = 0
    manifest_output: pathlib.Path | None = None
    result_output: pathlib.Path | None = None
    lock: pathlib.Path | None = None
    sidecar: pathlib.Path | None = None
    memory_backend: str = "host-sparse"
    host_request_number: int | None = None
    host_request_argument0: int | None = None
    service_request_type: int | None = None
    timeout_seconds: float | None = None
    deadline_monotonic: float | None = None


class ASLRefTimeoutError(RuntimeError):
    """The ASLRef execution subprocess exceeded its caller-owned deadline."""


def _run_aslref(command: list[str], timeout_seconds: float | None) -> subprocess.CompletedProcess[str]:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("ASLRef timeout_seconds must be positive")
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise ASLRefTimeoutError(
            f"ASLRef execution timed out after {timeout_seconds:.3f} seconds"
        ) from error


def _execution_timeout(configuration: RunConfiguration) -> float | None:
    timeout = configuration.timeout_seconds
    if configuration.deadline_monotonic is not None:
        remaining = configuration.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise ASLRefTimeoutError(
                "ASLRef execution deadline expired before process launch"
            )
        timeout = remaining if timeout is None else min(timeout, remaining)
    return timeout


def _checked_range(start: int, size: int, limit: int, label: str) -> tuple[int, int]:
    if start < 0 or size < 0 or start > limit or size > limit - start:
        raise ElfError(f"{label} range is outside reference memory")
    return start, start + size


def parse_elf(path: pathlib.Path,
              memory_bytes: int = REFERENCE_MEMORY_BYTES,
              *, require_pto_identity: bool = False) -> ElfImage:
    if memory_bytes < 256 or memory_bytes > MAX_HOSTED_MEMORY_BYTES:
        raise ElfError("hosted memory bound is outside the supported model profile")
    content = path.read_bytes()
    if len(content) < ELF_HEADER.size:
        raise ElfError("ELF header is truncated")
    fields = ELF_HEADER.unpack_from(content)
    identification = fields[0]
    if identification[:4] != ELF_MAGIC:
        raise ElfError("ELF magic mismatch")
    if identification[4] != ELF_CLASS_64 or identification[5] != ELF_DATA_LITTLE:
        raise ElfError("requires ELF64 little-endian input")
    if identification[6] != ELF_VERSION_CURRENT:
        raise ElfError("unsupported ELF identification version")
    elf_type, machine, version = fields[1:4]
    entry, program_offset, section_offset = fields[4:7]
    header_size, program_entry_size, program_count = fields[8:11]
    section_entry_size, section_count = fields[11:13]
    if elf_type != ELF_TYPE_EXEC:
        raise ElfError("requires static ET_EXEC input")
    if machine != PTO_ELF_MACHINE:
        raise ElfError("unexpected ELF machine")
    if version != ELF_VERSION_CURRENT or header_size != ELF_HEADER.size:
        raise ElfError("unsupported ELF header version or size")
    if program_entry_size != PROGRAM_HEADER.size:
        raise ElfError("unexpected program-header size")
    if section_count and section_entry_size != SECTION_HEADER.size:
        raise ElfError("unexpected section-header size")
    table_size = program_entry_size * program_count
    if program_offset > len(content) or table_size > len(content) - program_offset:
        raise ElfError("program-header table is truncated")

    segments: list[LoadSegment] = []
    ranges: list[tuple[int, int]] = []
    for index in range(program_count):
        offset = program_offset + index * program_entry_size
        (kind, flags, file_offset, virtual_address, physical_address,
         file_size, memory_size, alignment) = PROGRAM_HEADER.unpack_from(content, offset)
        if kind != PROGRAM_TYPE_LOAD:
            continue
        if flags & ~(PROGRAM_FLAG_READ | PROGRAM_FLAG_WRITE | PROGRAM_FLAG_EXECUTE):
            raise ElfError("PT_LOAD contains unknown permission bits")
        if file_size > memory_size:
            raise ElfError("PT_LOAD file size exceeds memory size")
        if file_offset > len(content) or file_size > len(content) - file_offset:
            raise ElfError("PT_LOAD file range is truncated")
        if virtual_address != physical_address:
            raise ElfError("initial profile requires identical virtual and physical addresses")
        start, end = _checked_range(
            virtual_address,
            memory_size,
            memory_bytes,
            "PT_LOAD",
        )
        if alignment not in (0, 1) and virtual_address % alignment != file_offset % alignment:
            raise ElfError("PT_LOAD alignment congruence failed")
        for previous_start, previous_end in ranges:
            if start < previous_end and previous_start < end:
                raise ElfError("PT_LOAD ranges overlap")
        ranges.append((start, end))
        payload = content[file_offset:file_offset + file_size]
        segments.append(LoadSegment(start, payload, memory_size, flags, alignment))

    if not segments:
        raise ElfError("ELF contains no PT_LOAD segments")
    executable_entry = any(
        segment.address <= entry < segment.end
        and segment.flags & PROGRAM_FLAG_EXECUTE
        for segment in segments
    )
    if not executable_entry:
        raise ElfError("ELF entry is outside an executable PT_LOAD")

    symbols: list[ElfSymbol] = []
    section_table_size = section_entry_size * section_count
    if section_count:
        if (section_offset > len(content)
                or section_table_size > len(content) - section_offset):
            raise ElfError("section-header table is truncated")
        sections = [
            SECTION_HEADER.unpack_from(content, section_offset + index * section_entry_size)
            for index in range(section_count)
        ]
        for section in sections:
            section_type = section[1]
            if section_type not in SECTION_TYPE_SYMBOLS:
                continue
            symbol_offset, symbol_size = section[4], section[5]
            string_index, entry_size = section[6], section[9]
            if entry_size != SYMBOL_ENTRY.size or string_index >= section_count:
                raise ElfError("invalid ELF symbol table metadata")
            string_section = sections[string_index]
            string_offset, string_size = string_section[4], string_section[5]
            if (symbol_offset > len(content)
                    or symbol_size > len(content) - symbol_offset
                    or string_offset > len(content)
                    or string_size > len(content) - string_offset):
                raise ElfError("ELF symbol or string table is truncated")
            strings = content[string_offset:string_offset + string_size]
            if symbol_size % entry_size:
                raise ElfError("ELF symbol table has a partial entry")
            for offset in range(symbol_offset, symbol_offset + symbol_size, entry_size):
                name_offset, _info, _other, symbol_section, value, size = (
                    SYMBOL_ENTRY.unpack_from(content, offset)
                )
                if name_offset >= len(strings):
                    raise ElfError("ELF symbol name is outside its string table")
                name_end = strings.find(b"\0", name_offset)
                if name_end < 0:
                    raise ElfError("ELF symbol name is unterminated")
                name = strings[name_offset:name_end].decode("utf-8", errors="strict")
                if name:
                    symbols.append(ElfSymbol(name, value, size, symbol_section))
    note = parse_pto_isa_note(path) if require_pto_identity else None
    return ElfImage(
        entry=entry,
        segments=tuple(sorted(segments, key=lambda segment: segment.address)),
        symbols=tuple(symbols),
        sha256=hashlib.sha256(content).hexdigest(),
        pto_isa_note=note,
    )


def _word(value: int) -> str:
    if value < 0 or value >= 1 << 64:
        raise ValueError("word value is outside 64 bits")
    return f"Zeros{{PTO_XLEN}} + 0x{value:x}"


def build_harness(image: ElfImage, configuration: RunConfiguration) -> str:
    if configuration.max_steps <= 0:
        raise ValueError("max_steps must be positive")
    if configuration.stop_after_hits <= 0:
        raise ValueError("stop_after_hits must be positive")
    if configuration.start_acr < 0 or configuration.start_acr > 15:
        raise ValueError("start_acr must be a four-bit access-control ring")
    _checked_range(
        configuration.result_address,
        configuration.result_size,
        configuration.memory_bytes,
        "result",
    )
    for segment in image.segments:
        _checked_range(
            segment.address,
            segment.memory_size,
            configuration.memory_bytes,
            "PT_LOAD",
        )
    start_pc = configuration.start_pc or image.entry
    executable_start = any(
        segment.address <= start_pc < segment.end
        and segment.flags & PROGRAM_FLAG_EXECUTE
        for segment in image.segments
    )
    if not executable_start:
        raise ElfError("hosted start PC is outside an executable PT_LOAD")
    lines = [
        "func main() => integer",
        "begin",
        "    ResetProfileState();",
        f"    SetCurrentACR({configuration.start_acr});",
        f"    WriteTPC({_word(start_pc)});",
        "    var model_stop_hits: integer = 0;",
        "    var previous_pc: bits(PTO_XLEN) = ReadTPC();",
        "    var previous_previous_pc: bits(PTO_XLEN) = ReadTPC();",
    ]
    if configuration.return_pc:
        lines.extend([
            f"    _ReturnAddress = {_word(configuration.return_pc)};",
            "    WritePEGPR(0, "
            f"{PTO_HOSTED_RA_GPR}, {_word(configuration.return_pc)});",
        ])
    if configuration.stack_top:
        lines.append(
            "    WritePEGPR(0, "
            f"{PTO_HOSTED_SP_GPR}, {_word(configuration.stack_top)});"
        )
    for segment in image.segments:
        initialized = segment.data + bytes(segment.memory_size - len(segment.data))
        for offset, value in enumerate(initialized):
            if value:
                lines.append(
                    "    WritePhysicalMemoryByte("
                    f"{_word(segment.address + offset)}, Zeros{{8}} + 0x{value:02x});"
                )
    rejection_diagnostics: list[str] = []
    if configuration.stack_top:
        rejection_diagnostics.extend([
            '            println "PTO_DIAG_STACK_RA ",',
            "                UInt(LoadTranslatedUnsigned("
            f"{_word(configuration.stack_top - 8)}, 8));",
            '            println "PTO_DIAG_STACK_S0 ",',
            "                UInt(LoadTranslatedUnsigned("
            f"{_word(configuration.stack_top - 16)}, 8));",
        ])
    lines.extend([
        f"    for model_step = 0 to {configuration.max_steps - 1} do",
        "        let step_pc = ReadTPC();",
        "        let status = ExecuteNextPTOInstruction();",
    ])
    if configuration.host_request_number is not None:
        if (
            configuration.host_request_argument0 is None
            or configuration.service_request_type is None
            or configuration.return_pc == 0
        ):
            raise ValueError("host request terminal policy is incomplete")
        if configuration.service_request_type > 15:
            raise ValueError("service request type is outside four bits")
        lines.extend([
            "        if status == PTOInstruction_Rejected &&",
            "           _LastFault == Fault_ServiceRequest &&",
            f"           UInt(_ControlRequestOperand[3:0]) == {configuration.service_request_type} &&",
            "           _TrapContexts[[CurrentACR()]].tpc == "
            f"{_word(configuration.return_pc)} &&",
            f"           UInt(ReadPEGPR(0, 9)) == {configuration.host_request_number} &&",
            f"           UInt(ReadPEGPR(0, 2)) == {configuration.host_request_argument0} then",
        ])
        if configuration.result_size:
            lines.extend([
                f"            for result_index = 0 to {configuration.result_size - 1} do",
                '                println "PTO_RESULT_BYTE ", result_index, " ",',
                "                    UInt(ReadPhysicalMemoryByte(",
                f"                        {_word(configuration.result_address)} + NaturalToWord(result_index)));",
                "            end;",
            ])
        lines.extend([
            '            println "PTO_FINAL_TPC ",',
            "                UInt(_TrapContexts[[CurrentACR()]].tpc);",
            "            return 0;",
            "        end;",
        ])
    lines.extend([
        "        if status == PTOInstruction_Rejected then",
        '            println "PTO_REJECTED_TPC ", UInt(step_pc);',
        '            println "PTO_PREVIOUS_TPC ", UInt(previous_pc);',
        '            println "PTO_PREVIOUS_PREVIOUS_TPC ",',
        "                UInt(previous_previous_pc);",
        '            println "PTO_FAULT_STATUS ",',
        "                UInt(PackTrapStatus(CurrentACR()));",
        '            println "PTO_DIAG_FAULT_CODE ", _LastFault;',
        '            println "PTO_FAULT_ADDRESS ", UInt(_FaultAddress);',
        '            println "PTO_DIAG_SP ", UInt(ReadPEGPR(0, 1));',
        '            println "PTO_DIAG_RA ", UInt(ReadPEGPR(0, 10));',
        '            println "PTO_DIAG_RETURN_ADDRESS ", UInt(_ReturnAddress);',
        '            if _BundleActive then',
        '                println "PTO_DIAG_BUNDLE_ACTIVE 1";',
        '                println "PTO_DIAG_MGATHER_SELECTED ",',
        '                    if BundleMGATHERSelected() then 1 else 0;',
        '                println "PTO_DIAG_MSCATTER_SELECTED ",',
        '                    if BundleMSCATTERSelected() then 1 else 0;',
        '                println "PTO_DIAG_TILE_BINDINGS ",',
        '                    BundleTileBindingCount();',
        '                println "PTO_DIAG_SHARED_BINDINGS ",',
        '                    BundleSharedBindingCount();',
        '                println "PTO_DIAG_SCALAR0_VALID ",',
        '                    if _BundleScalarBindings[[0]].valid then 1 else 0;',
        '                if _BundleScalarBindings[[0]].valid then',
        '                    println "PTO_DIAG_SCALAR_SOURCE0 ",',
        '                        _BundleScalarBindings[[0]].source0;',
        '                    println "PTO_DIAG_SCALAR_SOURCE1 ",',
        '                        _BundleScalarBindings[[0]].source1;',
        '                    println "PTO_DIAG_GPR_BASE ",',
        '                        UInt(ReadPEAbsoluteGPROperand(_CurrentMemoryAgent,',
        '                            _BundleScalarBindings[[0]].source0));',
        '                    println "PTO_DIAG_GPR_STRIDE ",',
        '                        UInt(ReadPEAbsoluteGPROperand(_CurrentMemoryAgent,',
        '                            _BundleScalarBindings[[0]].source1));',
        '                end;',
        '                println "PTO_DIAG_DIM0_PRESENT ",',
        '                    if _BundleDimensionPresent[[0]] then 1 else 0;',
        '                println "PTO_DIAG_DIM0 ", UInt(_BundleDimensions[[0]]);',
        '                println "PTO_DIAG_DIM1 ", UInt(_BundleDimensions[[1]]);',
        '                println "PTO_DIAG_DIM2 ", UInt(_BundleDimensions[[2]]);',
        '                let diagnostic_tile_operation =',
        '                    BundleTileOperationSelected() &&',
        '                    _BundleOperation.data_type_valid;',
        '                println "PTO_DIAG_TILE_OPERATION_SELECTED ",',
        '                    if diagnostic_tile_operation then 1 else 0;',
        '                if diagnostic_tile_operation then',
        '                    println "PTO_DIAG_DATA_TYPE_CODE ",',
        '                        UInt(CurrentBundleTileOperationDataTypeCode());',
        '                println "PTO_DIAG_DATR_PRESENT ",',
        '                    if _BundleDataAttributesPresent then 1 else 0;',
        '                println "PTO_DIAG_DATR_CMODE ",',
        '                    UInt(_BundleDataAttributes.comparison_mode);',
        '                println "PTO_DIAG_DATR_PAD ",',
        '                    UInt(_BundleDataAttributes.pad_value);',
        '                println "PTO_DIAG_DATR_SAT ",',
        '                    if _BundleDataAttributes.saturating then 1 else 0;',
        '                println "PTO_DIAG_DATR_CANON ",',
        '                    if _BundleDataAttributes.canonicalize then 1 else 0;',
        '                println "PTO_DIAG_DATR_TYPE ",',
        '                    UInt(_BundleDataAttributes.data_type);',
        '                println "PTO_DIAG_DATR_RMODE ",',
        '                    UInt(_BundleDataAttributes.rounding_mode);',
        '                println "PTO_DIAG_DATR_LAYOUT ",',
        '                    UInt(_BundleDataAttributes.data_layout);',
        '                println "PTO_DIAG_DIMENSIONS_LEGAL ",',
        '                    if BundleMGATHERDimensionsLegal() then 1 else 0;',
        '                println "PTO_DIAG_MASKS_LEGAL ",',
        '                    if SelectedBundleTileMasksLegal() then 1 else 0;',
        '                let diagnostic_family = BundleTileDecodeFamily(',
        '                    _BundleOperation.operation_class);',
        '                let diagnostic_code = BundleOperationDecodeCode(',
        '                    _BundleOperation);',
        '                let diagnostic_decoded = DecodeTileOperation(',
        '                    diagnostic_family, diagnostic_code);',
        '                println "PTO_DIAG_DECODED_OPERATION ", diagnostic_decoded;',
        '                if diagnostic_decoded != PTO_TILE_OPERATION_COUNT then',
        '                    let diagnostic_operation = diagnostic_decoded as',
        '                        integer {0..PTO_TILE_OPERATION_COUNT-1};',
        '                    println "PTO_DIAG_BINDINGS_COMPLETE ",',
        '                        if BundleOperationBindingsComplete(',
        '                            diagnostic_operation) then 1 else 0;',
        '                    println "PTO_DIAG_GPR_BINDINGS_LEGAL ",',
        '                        if BundleOperationGPRBindingValuesLegal(',
        '                            diagnostic_operation) then 1 else 0;',
        '                end;',
        '                println "PTO_DIAG_MSCATTER_BINDINGS_LEGAL ",',
        '                    if BundleMSCATTERBindingsLegal() then 1 else 0;',
        '                if BundleTileBindingCount() > 0 then',
        '                    println "PTO_DIAG_BINDING_DEST_VALID ",',
        '                        if _BundleTileBindings[[0]].destination_valid then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_SOURCE0_VALID ",',
        '                        if _BundleTileBindings[[0]].source0_valid then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_SOURCE1_VALID ",',
        '                        if _BundleTileBindings[[0]].source1_valid then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_LAST ",',
        '                        if _BundleTileBindings[[0]].last then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_ASSEMBLE_VALID ",',
        '                        if _BundleTileBindings[[0]].destination_assemble.valid then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_ASSEMBLE_INIT ",',
        '                        if _BundleTileBindings[[0]].destination_assemble.init then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_ASSEMBLE_LAST ",',
        '                        if _BundleTileBindings[[0]].destination_assemble.last then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_DEST_SIZE ",',
        '                        _BundleTileBindings[[0]].destination_size;',
        '                    println "PTO_DIAG_DEST_CAPACITY_BYTES ",',
        '                        BundleLocalDestinationAllocationBytes(0);',
        '                    println "PTO_DIAG_TILE_CAPACITY_LIMIT ",',
        '                        TileCapacityLimitBytes();',
        '                    println "PTO_DIAG_TILE_CAPACITY_IN_USE_PE0 ",',
        '                        TileCapacityInUseForPE(0);',
        '                    println "PTO_DIAG_DEST_CAPACITY_GROUP_FITS ",',
        '                        if BundleLocalDestinationCapacityGroupFits()',
        '                            then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_DEST_HAND ",',
        '                        UInt(_BundleTileBindings[[0]].destination_hand);',
        '                    println "PTO_DIAG_LOCAL_GENERATION_OPEN ",',
        '                        if BundleLocalGenerationOpenForHand(',
        '                            UInt(_BundleTileBindings[[0]].destination_hand)',
        '                                as integer {0..3}) then 1 else 0;',
        '                    println "PTO_DIAG_BINDING_DEST ",',
        '                        _BundleTileBindings[[0]].destination;',
        '                    println "PTO_DIAG_BINDING_SOURCE0 ",',
        '                        _BundleTileBindings[[0]].source0;',
        '                    println "PTO_DIAG_BINDING_SOURCE1 ",',
        '                        _BundleTileBindings[[0]].source1;',
        '                    if _BundleTileBindings[[0]].source0_valid then',
        '                        let source0 = _BundleTileBindings[[0]].source0;',
        '                        println "PTO_DIAG_SOURCE0_ALLOCATED ",',
        '                            if _Tiles[[source0]].allocated then 1 else 0;',
        '                        println "PTO_DIAG_SOURCE0_DEFINED ",',
        '                            if TileSourceContentsDefined(source0) then 1 else 0;',
        '                        println "PTO_DIAG_SOURCE0_TYPE ",',
        '                            UInt(TileDataTypeToEncoding(_Tiles[[source0]].data_type));',
        '                        println "PTO_DIAG_SOURCE0_ROWS ", _Tiles[[source0]].valid_rows;',
        '                        println "PTO_DIAG_SOURCE0_COLUMNS ", _Tiles[[source0]].valid_columns;',
        '                        println "PTO_DIAG_SOURCE0_PHYSICAL_COLUMNS ", _Tiles[[source0]].columns;',
        '                    end;',
        '                    if _BundleTileBindings[[0]].source1_valid then',
        '                        let source1 = _BundleTileBindings[[0]].source1;',
        '                        println "PTO_DIAG_SOURCE1_ALLOCATED ",',
        '                            if _Tiles[[source1]].allocated then 1 else 0;',
        '                        println "PTO_DIAG_SOURCE1_DEFINED ",',
        '                            if TileSourceContentsDefined(source1) then 1 else 0;',
        '                        println "PTO_DIAG_SOURCE1_TYPE ",',
        '                            UInt(TileDataTypeToEncoding(_Tiles[[source1]].data_type));',
        '                        println "PTO_DIAG_SOURCE1_ROWS ", _Tiles[[source1]].valid_rows;',
        '                        println "PTO_DIAG_SOURCE1_COLUMNS ", _Tiles[[source1]].valid_columns;',
        '                        println "PTO_DIAG_SOURCE1_PHYSICAL_COLUMNS ", _Tiles[[source1]].columns;',
        '                    end;',
        '                    if BundleMGATHERSelected() &&',
        '                       _BundleDimensionPresent[[0]] &&',
        '                       UInt(_BundleDimensions[[0]]) > 0 &&',
        '                       UInt(_BundleDimensions[[0]]) <= 65535 then',
        '                        let diagnostic_valid_columns =',
        '                            UInt(_BundleDimensions[[0]]) as',
        '                                integer {1..65535};',
        '                        let diagnostic_valid_rows = if',
        '                            _BundleDimensionPresent[[1]] then',
        '                                UInt(_BundleDimensions[[1]]) as',
        '                                    integer {1..65535}',
        '                            else 1;',
        '                        let diagnostic_columns = if',
        '                            _BundleDimensionPresent[[2]] then',
        '                                UInt(_BundleDimensions[[2]]) as',
        '                                    integer {1..65535}',
        '                            else diagnostic_valid_columns;',
        '                        let diagnostic_data_type =',
        '                            TileDataTypeFromEncoding(',
        '                                CurrentBundleTileOperationDataTypeCode()',
        '                                    as TileDataTypeEncoding);',
        '                        println "PTO_DIAG_INDEX_TYPE_LEGAL ",',
        '                            if IndexedTLSUIndexDataTypeLegal(',
        '                                _Tiles[[_BundleTileBindings[[0]].source0]].data_type)',
        '                                then 1 else 0;',
        '                        println "PTO_DIAG_TRANSFER_TYPE_LEGAL ",',
        '                            if IndexedTLSUTransferDataTypeLegal(',
        '                                diagnostic_data_type) then 1 else 0;',
        '                        println "PTO_DIAG_SOURCE_SHAPE_MATCH ",',
        '                            if _Tiles[[_BundleTileBindings[[0]].source0]].valid_rows ==',
        '                                   diagnostic_valid_rows &&',
        '                               _Tiles[[_BundleTileBindings[[0]].source0]].valid_columns ==',
        '                                   diagnostic_valid_columns then 1 else 0;',
        '                        let diagnostic_resolved =',
        '                            ResolveBundleTileDestinationsWithShapeAndType(',
        '                                TRUE, diagnostic_valid_rows,',
        '                                diagnostic_valid_columns, diagnostic_columns,',
        '                                TRUE, diagnostic_data_type);',
        '                        println "PTO_DIAG_DEST_RESOLVED ",',
        '                            if diagnostic_resolved then 1 else 0;',
        '                        if diagnostic_resolved then',
        '                            let diagnostic_destination =',
        '                                _BundleTileBindings[[0]].destination;',
        '                            println "PTO_DIAG_DEST_DESCRIPTOR_LEGAL ",',
        '                                if TileDescriptorLegal(diagnostic_destination)',
        '                                    then 1 else 0;',
        '                            println "PTO_DIAG_DEST_LAYOUT ",',
        '                                _Tiles[[diagnostic_destination]].layout;',
        '                            println "PTO_DIAG_DEST_TYPE ",',
        '                                UInt(TileDataTypeToEncoding(',
        '                                    _Tiles[[diagnostic_destination]].data_type));',
        '                            println "PTO_DIAG_TILE_OPERANDS_LEGAL ",',
        '                                if TileOperandsLegal_MGATHER(',
        '                                    diagnostic_destination, Zeros{PTO_XLEN},',
        '                                    ReadPEAbsoluteGPROperand(_CurrentMemoryAgent,',
        '                                        _BundleScalarBindings[[0]].source1),',
        '                                    _BundleTileBindings[[0]].source0,',
        '                                    CurrentBundlePadValue()) then 1 else 0;',
        '                            RollBackBundleTileDestinations();',
        '                        end;',
        '                    end;',
        '                end;',
        '                end;',
        '            else',
        '                println "PTO_DIAG_BUNDLE_ACTIVE 0";',
        '            end;',
        *rejection_diagnostics,
        "            return 2;",
        "        end;",
        "        previous_previous_pc = previous_pc;",
        "        previous_pc = step_pc;",
        f"        if ReadTPC() == {_word(configuration.stop_pc)} then",
        "            model_stop_hits = model_stop_hits + 1;",
        f"            if model_stop_hits == {configuration.stop_after_hits} then",
        f"                for result_index = 0 to {configuration.result_size - 1} do"
        if configuration.result_size else "                pass;",
    ])
    if configuration.result_size:
        lines.extend([
            '                    println "PTO_RESULT_BYTE ", result_index, " ",',
            "                        UInt(ReadPhysicalMemoryByte("
            f"                            {_word(configuration.result_address)} +"
            " NaturalToWord(result_index)));",
            "                end;",
        ])
    lines.extend([
        '                println "PTO_FINAL_TPC ", UInt(ReadTPC());',
        "                return 0;",
        "            end;",
        "        end;",
        "    end;",
        '    println "PTO_STEP_LIMIT";',
        "    return 3;",
        "end;",
        "",
    ])
    return "\n".join(lines)


def specialize_reference_profile(asl_spec: bytes, memory_bytes: int,
                                 tile_elements: int,
                                 *,
                                 fresh_process_reset: bool = False) -> bytes:
    """Select bounded hosted storage without changing PTO semantics."""
    if memory_bytes < 256 or memory_bytes > MAX_HOSTED_MEMORY_BYTES:
        raise ValueError("hosted memory bound is outside the supported model profile")
    if tile_elements < 1 or tile_elements > REFERENCE_TILE_ELEMENTS:
        raise ValueError("hosted Tile bound is outside the supported model profile")
    replacement = (
        "config PTO_MODEL_MEMORY_BYTES : integer "
        f"{{256..{memory_bytes}}} = {memory_bytes};"
    ).encode("ascii")
    specialized, count = MEMORY_CONFIG_RE.subn(replacement, asl_spec)
    if count != 1:
        raise ValueError("PTO ASL memory configuration was not found exactly once")
    tile_replacement = (
        "config PTO_MODEL_TILE_ELEMENTS : integer "
        f"{{1..{REFERENCE_TILE_ELEMENTS}}} = {tile_elements};"
    ).encode("ascii")
    specialized, count = TILE_CONFIG_RE.subn(tile_replacement, specialized)
    if count != 1:
        raise ValueError("PTO ASL Tile configuration was not found exactly once")
    if fresh_process_reset:
        specialized, count = MEMORY_RESET_LOOP_RE.subn(
            b"    // The hosted runner executes one ELF in a fresh ASLRef "
            b"process.\n"
            b"    // Fresh interpreter storage is already zero; retain full "
            b"reset for\n"
            b"    // every architectural state domain except the redundant "
            b"memory sweep.\n"
            b"    pass;",
            specialized,
        )
        if count != 1:
            raise ValueError(
                "PTO ASL memory reset loop was not found exactly once"
            )
    return specialized


def _load_lock(path: pathlib.Path | None) -> dict[str, object]:
    if path is None:
        raise ValueError("an exact model lock is required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "pto-asl-model-lock-v1":
        raise ValueError("model lock schema mismatch")
    expected_release = {
        "architecture_version": RELEASE,
        "publication_version": PUBLICATION_VERSION,
        "encoding_abi": ENCODING_ABI,
        "encoding_projection_sha256": ENCODING_PROJECTION_SHA256,
    }
    for field, expected in expected_release.items():
        if value.get(field) != expected:
            raise ValueError(f"model lock {field} mismatch")
    return value


def _git_root(path: pathlib.Path) -> pathlib.Path:
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / ".git").exists():
            return directory
    raise ValueError(f"cannot resolve a git checkout for {path}")


def _git_value(root: pathlib.Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "git identity query failed")
    return completed.stdout.strip()


def _file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_path(path: pathlib.Path, label: str) -> pathlib.Path:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    return path


def _host_memory_runner(
    aslref: pathlib.Path,
) -> tuple[pathlib.Path, dict[str, str]]:
    """Build or reuse the model-owned ASLRef host-memory executable."""
    aslref_root = _git_root(aslref)
    asllib = aslref_root / "_build" / "default" / "asllib"
    cmxa = _required_path(asllib / "asllib.cmxa", "ASLRef asllib.cmxa")
    byte_cmi = asllib / ".asllib.objs" / "byte"
    native_cmi = asllib / ".asllib.objs" / "native"
    if not byte_cmi.is_dir() or not native_cmi.is_dir():
        raise ValueError("missing ASLRef native interface directories")
    source = _required_path(
        HOST_MEMORY_RUNNER_SOURCE, "host-memory runner source"
    )
    opam = shutil.which("opam")
    if opam is None:
        raise ValueError("opam is required to build the host-memory runner")
    prefix_query = subprocess.run(
        [opam, "var", "prefix"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if prefix_query.returncode != 0:
        raise ValueError(
            prefix_query.stderr.strip() or "cannot resolve the opam prefix"
        )
    opam_prefix = pathlib.Path(prefix_query.stdout.strip())
    ocamlopt = _required_path(
        opam_prefix / "bin" / "ocamlopt", "opam ocamlopt"
    )
    zarith = opam_prefix / "lib" / "zarith"
    menhir = opam_prefix / "lib" / "menhirLib"
    dependencies = (
        _required_path(zarith / "zarith.cmxa", "zarith.cmxa"),
        _required_path(menhir / "menhirLib.cmxa", "menhirLib.cmxa"),
    )
    compiler_version = subprocess.run(
        [str(ocamlopt), "-version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    identity = {
        "schema": HOST_MEMORY_RUNNER_SCHEMA,
        "aslref_commit": _git_value(aslref_root, "rev-parse", "HEAD"),
        "asllib_sha256": _file_sha256(cmxa),
        "source_sha256": _file_sha256(source),
        "ocaml_version": compiler_version,
    }
    cache_key = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_root = pathlib.Path(
        os.environ.get(
            "PTO_ASL_MODEL_CACHE",
            pathlib.Path.home() / ".cache" / "pto-asl-model",
        )
    ).expanduser()
    target = cache_root / "host-memory-runner" / cache_key
    executable = target / "aslref-host-memory"
    metadata = target / "identity.json"
    if executable.is_file() and os.access(executable, os.X_OK) and metadata.is_file():
        try:
            if json.loads(metadata.read_text(encoding="utf-8")) == identity:
                return executable, identity
        except (OSError, json.JSONDecodeError):
            pass

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=target) as directory:
        build = pathlib.Path(directory)
        build_source = build / source.name
        shutil.copy2(source, build_source)
        temporary_executable = build / executable.name
        command = [str(ocamlopt)]
        gmp_directories = [
            pathlib.Path("/opt/homebrew/lib"),
            pathlib.Path.home() / ".local" / "lib",
            pathlib.Path("/usr/lib/x86_64-linux-gnu"),
        ]
        gmp_directory = next(
            (item for item in gmp_directories
             if (item / "libgmp.a").is_file()
             or (item / "libgmp.so").is_file()
             or (item / "libgmp.dylib").is_file()),
            None,
        )
        if gmp_directory is not None:
            command.extend(("-ccopt", "-L" + str(gmp_directory)))
        if sys.platform == "darwin":
            command.extend(("-cclib", "-Wl,-stack_size,0x20000000"))
        for include in (byte_cmi, native_cmi, zarith, menhir):
            command.extend(("-I", str(include)))
        command.extend((
            "-o", str(temporary_executable),
            *(str(item) for item in dependencies),
            str(cmxa), str(build_source),
        ))
        completed = subprocess.run(
            command,
            cwd=build,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0 or not temporary_executable.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise ValueError(
                "failed to build the host-memory runner"
                + (f": {detail[-4000:]}" if detail else "")
            )
        shutil.copy2(temporary_executable, executable)
    executable.chmod(0o755)
    metadata.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return executable, identity


def _verify_identity(configuration: RunConfiguration,
                     lock: dict[str, object]) -> None:
    pto_root = _git_root(configuration.asl_spec)
    aslref_root = _git_root(configuration.aslref)
    expected = {
        "pto_commit": _git_value(pto_root, "rev-parse", "HEAD"),
        "pto_tree": _git_value(pto_root, "rev-parse", "HEAD^{tree}"),
        "pto_repository": canonical_repository_url(
            _git_value(pto_root, "remote", "get-url", "origin")
        ),
        "aslref_commit": _git_value(aslref_root, "rev-parse", "HEAD"),
        "aslref_repository": canonical_repository_url(
            _git_value(aslref_root, "remote", "get-url", "origin")
        ),
    }
    for field, actual in expected.items():
        if lock.get(field) != actual:
            raise ValueError(
                f"model identity mismatch for {field}: "
                f"expected {lock.get(field)!r}, got {actual!r}"
            )
    pin = (pto_root / ".aslref-version").read_text(encoding="utf-8").strip()
    if pin != lock.get("aslref_commit"):
        raise ValueError("PTO ASLRef pin does not match model lock")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"sidecar {label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"sidecar {label} must be non-empty text")
    return value


def _natural(value: object, label: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"sidecar {label} must be a non-negative integer")
    if positive and value == 0:
        raise ValueError(f"sidecar {label} must be positive")
    return value


def _load_sidecar(configuration: RunConfiguration) -> tuple[
        RunConfiguration, dict[str, object] | None, str | None]:
    if configuration.sidecar is None:
        return configuration, None, None
    content = configuration.sidecar.read_bytes()
    document = _object(json.loads(content), "root")
    if document.get("schema") != SIDECAR_SCHEMA:
        raise ValueError("sidecar schema mismatch")
    model = _object(document.get("model"), "model")
    execution = _object(document.get("execution"), "execution")
    start = _object(document.get("start"), "start")
    result = _object(document.get("result"), "result")
    host_request = execution.get("host_request")
    if host_request is not None:
        host_request = _object(host_request, "execution.host_request")
    configured = dataclasses.replace(
        configuration,
        memory_bytes=_natural(model.get("memory_bytes"), "model.memory_bytes", positive=True),
        tile_elements=_natural(model.get("tile_elements"), "model.tile_elements", positive=True),
        runtime_typecheck=_text(
            model.get("runtime_typecheck", "minimal"), "model.runtime_typecheck"
        ),
        stop_pc=_natural(execution.get("stop_pc"), "execution.stop_pc"),
        stop_after_hits=_natural(
            execution.get("stop_after_hits"), "execution.stop_after_hits", positive=True
        ),
        max_steps=_natural(execution.get("max_steps"), "execution.max_steps", positive=True),
        stack_top=_natural(execution.get("stack_top"), "execution.stack_top"),
        start_pc=_natural(start.get("pc"), "start.pc"),
        start_acr=_natural(start.get("acr", 0), "start.acr"),
        return_pc=_natural(start.get("return_pc"), "start.return_pc"),
        result_address=_natural(result.get("address"), "result.address"),
        result_size=_natural(result.get("size"), "result.size", positive=True),
        host_request_number=(
            _natural(host_request.get("number"), "execution.host_request.number")
            if host_request is not None else None
        ),
        host_request_argument0=(
            _natural(host_request.get("argument0"), "execution.host_request.argument0")
            if host_request is not None else None
        ),
        service_request_type=(
            _natural(host_request.get("service_request_type"), "execution.host_request.service_request_type")
            if host_request is not None else None
        ),
    )
    return configured, document, hashlib.sha256(content).hexdigest()


def _unique_symbol(image: ElfImage, name: str) -> ElfSymbol:
    matches = [symbol for symbol in image.symbols if symbol.name == name]
    if len(matches) != 1:
        raise ValueError(f"ELF must contain exactly one {name!r} symbol")
    return matches[0]


def _validate_sidecar(configuration: RunConfiguration, image: ElfImage,
                      lock: dict[str, object], document: dict[str, object]) -> None:
    elf = _object(document.get("elf"), "elf")
    if pathlib.Path(_text(elf.get("path"), "elf.path")).name != configuration.elf.name:
        raise ValueError("sidecar ELF path does not match the selected ELF")
    if _text(elf.get("sha256"), "elf.sha256") != image.sha256:
        raise ValueError("sidecar ELF hash mismatch")
    if _natural(elf.get("machine"), "elf.machine") != PTO_ELF_MACHINE:
        raise ValueError("sidecar ELF machine mismatch")
    if _natural(elf.get("entry"), "elf.entry") != image.entry:
        raise ValueError("sidecar ELF entry mismatch")
    expected_segments = [
        {
            "address": segment.address,
            "filesz": len(segment.data),
            "memsz": segment.memory_size,
            "flags": segment.flags,
        }
        for segment in image.segments
    ]
    if elf.get("segments") != expected_segments:
        raise ValueError("sidecar PT_LOAD records mismatch")

    identity = _object(document.get("identity"), "identity")
    if identity.get("pto_commit") != lock.get("pto_commit"):
        raise ValueError("sidecar PTO identity mismatch")
    model = _object(document.get("model"), "model")
    if model.get("profile") != "bounded-reference-v1" or model.get("pe_count") != 1:
        raise ValueError("unsupported sidecar model profile or PE count")

    start = _object(document.get("start"), "start")
    start_symbol_name = _text(start.get("symbol"), "start.symbol")
    return_symbol_name = _text(start.get("return_symbol"), "start.return_symbol")
    if _unique_symbol(image, start_symbol_name).value != configuration.start_pc:
        raise ValueError("sidecar start symbol mismatch")
    if _unique_symbol(image, return_symbol_name).value != configuration.return_pc:
        raise ValueError("sidecar return symbol mismatch")

    execution = _object(document.get("execution"), "execution")
    stop_symbol_name = _text(execution.get("stop_symbol"), "execution.stop_symbol")
    if _unique_symbol(image, stop_symbol_name).value != configuration.stop_pc:
        raise ValueError("sidecar stop symbol mismatch")

    result = _object(document.get("result"), "result")
    result_symbol_name = _text(result.get("symbol"), "result.symbol")
    size_symbol_name = _text(result.get("size_symbol"), "result.size_symbol")
    result_symbol = _unique_symbol(image, result_symbol_name)
    size_symbol = _unique_symbol(image, size_symbol_name)
    if (result_symbol.value != configuration.result_address
            or result_symbol.size != configuration.result_size):
        raise ValueError("sidecar result symbol mismatch")
    if (size_symbol.section_index != SECTION_INDEX_ABSOLUTE
            or size_symbol.value != configuration.result_size):
        raise ValueError("sidecar result-size symbol mismatch")
    writable_result = any(
        segment.address <= configuration.result_address
        and configuration.result_address + configuration.result_size <= segment.end
        and segment.flags & PROGRAM_FLAG_WRITE
        for segment in image.segments
    )
    if not writable_result:
        raise ValueError("sidecar result range is not in one writable PT_LOAD")

    golden = _object(result.get("golden"), "result.golden")
    golden_path = configuration.sidecar.parent / _text(
        golden.get("path"), "result.golden.path"
    )
    golden_content = golden_path.read_bytes()
    if len(golden_content) != configuration.result_size:
        raise ValueError("golden result size mismatch")
    if hashlib.sha256(golden_content).hexdigest() != _text(
            golden.get("sha256"), "result.golden.sha256"):
        raise ValueError("golden result hash mismatch")


def parse_result(stdout: str, result_size: int) -> tuple[bytes, int]:
    result = bytearray(result_size)
    seen: set[int] = set()
    final_tpc: int | None = None
    for line in stdout.splitlines():
        result_match = RESULT_RE.fullmatch(line.strip())
        if result_match:
            index = int(result_match.group(1))
            value = int(result_match.group(2))
            if index >= result_size or value > 255 or index in seen:
                raise ValueError("malformed or duplicate result byte")
            result[index] = value
            seen.add(index)
        tpc_match = FINAL_TPC_RE.fullmatch(line.strip())
        if tpc_match:
            if final_tpc is not None:
                raise ValueError("duplicate final TPC marker")
            final_tpc = int(tpc_match.group(1))
    if len(seen) != result_size or final_tpc is None:
        raise ValueError("ASLRef output is missing result markers")
    return bytes(result), final_tpc


def run(configuration: RunConfiguration) -> dict[str, object]:
    run_started = time.perf_counter()
    configuration, sidecar_document, sidecar_sha256 = _load_sidecar(configuration)
    lock = _load_lock(configuration.lock)
    image = parse_elf(
        configuration.elf,
        configuration.memory_bytes,
        require_pto_identity=True,
    )
    _verify_identity(configuration, lock)
    if sidecar_document is not None:
        _validate_sidecar(configuration, image, lock, sidecar_document)
    harness = build_harness(image, configuration)
    if configuration.runtime_typecheck not in {"strict", "minimal"}:
        raise ValueError("runtime_typecheck must be strict or minimal")
    if configuration.memory_backend not in {"host-sparse", "reference-array"}:
        raise ValueError("memory_backend must be host-sparse or reference-array")
    typecheck_option = (
        "--type-check-strict"
        if configuration.runtime_typecheck == "strict"
        else "--no-type-check"
    )
    aslref_executable = configuration.aslref
    runner_backend = "aslref-reference-array-v1"
    runner_identity: dict[str, str] | None = None
    if (configuration.memory_backend == "host-sparse"
            and configuration.runtime_typecheck == "minimal"):
        aslref_executable, runner_identity = _host_memory_runner(
            configuration.aslref
        )
        runner_backend = HOST_MEMORY_RUNNER_SCHEMA
    aslref_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pto-asl-model-") as directory:
        combined = pathlib.Path(directory) / "model.asl"
        combined.write_bytes(
            specialize_reference_profile(
                configuration.asl_spec.read_bytes(),
                configuration.memory_bytes,
                configuration.tile_elements,
                fresh_process_reset=True,
            )
            + b"\n"
            + harness.encode("utf-8")
        )
        completed = _run_aslref(
            [
                "/bin/sh",
                "-c",
                'stack_limit=$(ulimit -H -s); ulimit -s "$stack_limit"; exec "$@"',
                "pto-aslref",
                str(aslref_executable),
                typecheck_option,
                str(combined),
            ],
            _execution_timeout(configuration),
        )
    aslref_elapsed_ms = (time.perf_counter() - aslref_started) * 1000.0
    if completed.returncode != 0:
        diagnostics = "\n".join(
            part for part in (completed.stdout.strip(), completed.stderr.strip())
            if part
        )
        raise RuntimeError(
            f"ASLRef execution failed with {completed.returncode} "
            f"after {aslref_elapsed_ms:.3f} ms: "
            + diagnostics
        )
    result, final_tpc = parse_result(completed.stdout, configuration.result_size)
    manifest: dict[str, object] = {
        "schema": "pto-asl-model-run-v1",
        "status": "passed",
        "elf": {
            "path": configuration.elf.name,
            "sha256": image.sha256,
            "entry": image.entry,
            "pto_isa_identity": image.pto_isa_note.as_dict()
            if image.pto_isa_note is not None else None,
            "pto_isa_descriptor_sha256": image.pto_isa_note.descriptor_sha256
            if image.pto_isa_note is not None else None,
            "segments": [
                {
                    "address": segment.address,
                    "filesz": len(segment.data),
                    "memsz": segment.memory_size,
                    "flags": segment.flags,
                }
                for segment in image.segments
            ],
        },
        "stop_policy": {
            "stop_pc": configuration.stop_pc,
            "stop_after_hits": configuration.stop_after_hits,
            "max_steps": configuration.max_steps,
            "host_request": (
                {
                    "number": configuration.host_request_number,
                    "argument0": configuration.host_request_argument0,
                    "service_request_type": configuration.service_request_type,
                }
                if configuration.host_request_number is not None else None
            ),
        },
        "start_policy": {
            "start_pc": configuration.start_pc or image.entry,
            "start_acr": configuration.start_acr,
            "return_pc": configuration.return_pc,
            "mode": "direct-boot" if configuration.start_pc else "elf-entry",
        },
        "model_profile": {
            "memory_bytes": configuration.memory_bytes,
            "tile_elements": configuration.tile_elements,
            "memory_storage": (
                "host-sparse-byte-map"
                if runner_identity is not None
                else "bounded-reference-array"
            ),
            "runner_backend": runner_backend,
            "runtime_typecheck": configuration.runtime_typecheck,
            "reset_policy": "fresh-process-zero-initial-memory",
        },
        "final_tpc": final_tpc,
        "result": {
            "address": configuration.result_address,
            "size": configuration.result_size,
            "bytes_hex": result.hex(),
            "sha256": hashlib.sha256(result).hexdigest(),
        },
        "identity": lock,
        "provenance": {
            "pto_asl_sha256": _file_sha256(configuration.asl_spec),
            "aslref_binary_sha256": _file_sha256(configuration.aslref),
            "model_runner_sha256": _file_sha256(pathlib.Path(__file__)),
        },
        "host_timing_ms": {
            "preflight": round((aslref_started - run_started) * 1000.0, 3),
            "aslref": round(aslref_elapsed_ms, 3),
            "total": round((time.perf_counter() - run_started) * 1000.0, 3),
        },
    }
    if sidecar_document is not None:
        manifest["sidecar"] = {
            "path": configuration.sidecar.name,
            "sha256": sidecar_sha256,
            "case_id": _text(sidecar_document.get("case_id"), "case_id"),
        }
    if runner_identity is not None:
        manifest["runner_identity"] = runner_identity
    if configuration.manifest_output is not None:
        configuration.manifest_output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if configuration.result_output is not None:
        configuration.result_output.write_bytes(result)
    return manifest


def _integer(text: str) -> int:
    return int(text, 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asl-spec", required=True, type=pathlib.Path)
    parser.add_argument("--aslref", required=True, type=pathlib.Path)
    parser.add_argument("--elf", required=True, type=pathlib.Path)
    parser.add_argument("--sidecar", type=pathlib.Path)
    parser.add_argument("--stop-pc", type=_integer, default=0)
    parser.add_argument("--stop-after-hits", type=int, default=1)
    parser.add_argument("--start-pc", type=_integer, default=0)
    parser.add_argument("--start-acr", type=int, default=0)
    parser.add_argument("--return-pc", type=_integer, default=0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--result-address", type=_integer, default=0)
    parser.add_argument("--result-size", type=int, default=0)
    parser.add_argument(
        "--memory-bytes", type=_integer, default=REFERENCE_MEMORY_BYTES
    )
    parser.add_argument(
        "--tile-elements", type=int, default=REFERENCE_TILE_ELEMENTS
    )
    parser.add_argument(
        "--runtime-typecheck", choices=("strict", "minimal"), default="strict"
    )
    parser.add_argument(
        "--memory-backend",
        choices=("host-sparse", "reference-array"),
        default="host-sparse",
    )
    parser.add_argument("--stack-top", type=_integer, default=0)
    parser.add_argument("--manifest-out", type=pathlib.Path)
    parser.add_argument("--result-out", type=pathlib.Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    parser.add_argument("--timeout-seconds", type=float)
    arguments = parser.parse_args(argv)
    manifest = run(RunConfiguration(
        asl_spec=arguments.asl_spec,
        aslref=arguments.aslref,
        elf=arguments.elf,
        sidecar=arguments.sidecar,
        stop_pc=arguments.stop_pc,
        stop_after_hits=arguments.stop_after_hits,
        start_pc=arguments.start_pc,
        start_acr=arguments.start_acr,
        return_pc=arguments.return_pc,
        max_steps=arguments.max_steps,
        result_address=arguments.result_address,
        result_size=arguments.result_size,
        memory_bytes=arguments.memory_bytes,
        tile_elements=arguments.tile_elements,
        runtime_typecheck=arguments.runtime_typecheck,
        stack_top=arguments.stack_top,
        manifest_output=arguments.manifest_out,
        result_output=arguments.result_out,
        lock=arguments.lock,
        memory_backend=arguments.memory_backend,
        timeout_seconds=arguments.timeout_seconds,
    ))
    if not arguments.quiet:
        print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
