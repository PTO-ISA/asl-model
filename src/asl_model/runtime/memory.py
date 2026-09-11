"""Deterministic guest-memory adapter used by the ASL functional runtime.

The semantic backend must not access host memory directly.  This module gives
it a small guest-address-space interface and an atomic write transaction.  A
native backend can implement the same interface over its existing memory
system; the mock implementation is intentionally useful in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class MemoryAccessError(RuntimeError):
    """Raised when a guest memory access is unmapped or lacks permission."""

    def __init__(self, kind: str, address: int, size: int, reason: str):
        self.kind = kind
        self.address = address
        self.size = size
        self.reason = reason
        super().__init__(
            f"guest memory {kind} denied at 0x{address:x} ({size} bytes): {reason}"
        )


@dataclass(frozen=True)
class GuestRegion:
    base: int
    size: int
    permissions: str = "rw"
    name: str = ""

    def __post_init__(self) -> None:
        if self.base < 0 or self.size <= 0:
            raise ValueError("guest region requires base >= 0 and size > 0")
        if any(flag not in "rwx" for flag in self.permissions):
            raise ValueError(f"invalid guest permissions: {self.permissions!r}")
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("guest permissions must not contain duplicate flags")

    @property
    def end(self) -> int:
        return self.base + self.size


@dataclass(frozen=True)
class MemorySnapshot:
    """Serializable point-in-time guest-memory state."""

    regions: tuple[GuestRegion, ...]
    bytes_by_address: tuple[tuple[int, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "regions": [
                {
                    "base": region.base,
                    "size": region.size,
                    "permissions": region.permissions,
                    "name": region.name,
                }
                for region in self.regions
            ],
            "bytes": {f"0x{address:x}": value for address, value in self.bytes_by_address},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MemorySnapshot":
        regions = tuple(GuestRegion(**dict(item)) for item in value.get("regions", []))
        raw_bytes = value.get("bytes", {})
        if not isinstance(raw_bytes, dict):
            raise ValueError("memory snapshot bytes must be an object")
        items = tuple(sorted((int(str(address), 0), int(byte)) for address, byte in raw_bytes.items()))
        return cls(regions=regions, bytes_by_address=items)


class GuestMemory:
    """Sparse, checked guest address space.

    Unwritten bytes in a mapped region read as zero.  Mapping is explicit and
    overlapping mappings are rejected, which makes malformed ELF loading
    visible instead of silently changing permissions or data.
    """

    def __init__(self) -> None:
        self._regions: list[GuestRegion] = []
        self._bytes: dict[int, int] = {}

    @property
    def regions(self) -> tuple[GuestRegion, ...]:
        return tuple(self._regions)

    def map_region(
        self,
        base: int,
        size: int,
        *,
        permissions: str = "rw",
        name: str = "",
        data: bytes | bytearray | memoryview = b"",
    ) -> GuestRegion:
        region = GuestRegion(base, size, permissions, name)
        payload = bytes(data)
        if len(payload) > size:
            raise ValueError("initial region data is larger than mapped region")
        if any(region.base < other.end and other.base < region.end for other in self._regions):
            raise ValueError(f"overlapping guest region at 0x{base:x}")
        self._regions.append(region)
        self._regions.sort(key=lambda item: item.base)
        if payload:
            # Segment initialization is a loader operation.  It may populate
            # read-only code/data mappings, while later guest writes still
            # obey the region permissions.
            self._write_unchecked(region.base, payload)
        return region

    def read(self, address: int, size: int) -> bytes:
        region = self._check(address, size, "r")
        del region
        return bytes(self._bytes.get(address + offset, 0) for offset in range(size))

    def write(self, address: int, data: bytes | bytearray | memoryview) -> None:
        payload = bytes(data)
        self._check(address, len(payload), "w")
        self._write_unchecked(address, payload)

    def read_u(self, address: int, size: int, *, byteorder: str = "little") -> int:
        if size <= 0:
            raise ValueError("integer read size must be positive")
        return int.from_bytes(self.read(address, size), byteorder)

    def write_u(self, address: int, value: int, size: int, *, byteorder: str = "little") -> None:
        if size <= 0 or value < 0 or value >= 1 << (size * 8):
            raise ValueError("integer write value does not fit requested size")
        self.write(address, value.to_bytes(size, byteorder))

    def begin(self) -> "MemoryTransaction":
        return MemoryTransaction(self)

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            regions=tuple(self._regions),
            bytes_by_address=tuple(sorted(self._bytes.items())),
        )

    def restore(self, snapshot: MemorySnapshot) -> None:
        if not isinstance(snapshot, MemorySnapshot):
            raise TypeError("guest memory restore requires MemorySnapshot")
        self._regions = list(snapshot.regions)
        self._bytes = dict(snapshot.bytes_by_address)

    def _check(self, address: int, size: int, permission: str) -> GuestRegion:
        if address < 0 or size < 0 or address + size < address:
            raise MemoryAccessError(permission, address, size, "invalid address range")
        if size == 0:
            for region in self._regions:
                if region.base <= address <= region.end and permission in region.permissions:
                    return region
            raise MemoryAccessError(permission, address, size, "unmapped address")
        for region in self._regions:
            if region.base <= address and address + size <= region.end:
                if permission not in region.permissions:
                    raise MemoryAccessError(permission, address, size, "permission denied")
                return region
        raise MemoryAccessError(permission, address, size, "unmapped or cross-region range")

    def _write_unchecked(self, address: int, payload: bytes) -> None:
        for offset, value in enumerate(payload):
            self._bytes[address + offset] = value


class MemoryTransaction:
    """Atomic guest-memory writes for one instruction transaction."""

    def __init__(self, memory: GuestMemory):
        self.memory = memory
        self._writes: list[tuple[int, bytes]] = []
        self._closed = False

    def read(self, address: int, size: int) -> bytes:
        self._check_open()
        result = bytearray(self.memory.read(address, size))
        for write_address, payload in self._writes:
            overlap_start = max(address, write_address)
            overlap_end = min(address + size, write_address + len(payload))
            if overlap_start < overlap_end:
                src_start = overlap_start - write_address
                result[overlap_start - address : overlap_end - address] = payload[
                    src_start : src_start + overlap_end - overlap_start
                ]
        return bytes(result)

    def write(self, address: int, data: bytes | bytearray | memoryview) -> None:
        self._check_open()
        payload = bytes(data)
        self.memory._check(address, len(payload), "w")
        self._writes.append((address, payload))

    def read_u(self, address: int, size: int, *, byteorder: str = "little") -> int:
        if size <= 0:
            raise ValueError("integer read size must be positive")
        return int.from_bytes(self.read(address, size), byteorder)

    def write_u(self, address: int, value: int, size: int, *, byteorder: str = "little") -> None:
        if size <= 0 or value < 0 or value >= 1 << (size * 8):
            raise ValueError("integer write value does not fit requested size")
        self.write(address, value.to_bytes(size, byteorder))

    def commit(self) -> tuple[tuple[int, bytes], ...]:
        self._check_open()
        for address, payload in self._writes:
            self.memory._write_unchecked(address, payload)
        self._closed = True
        return tuple(self._writes)

    def abort(self) -> None:
        self._check_open()
        self._writes.clear()
        self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("memory transaction is closed")


__all__ = ["GuestMemory", "GuestRegion", "MemoryAccessError", "MemorySnapshot", "MemoryTransaction"]
