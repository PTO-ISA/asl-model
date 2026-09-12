"""Transactional host-memory views for one parallel multi-PE round.

Each view observes the same point-in-time :class:`HostMemoryBridge` contents
and overlays only its own writes.  The coordinator delays all mutations of the
host bridge until it has established that deterministic PE-order execution
would not expose an earlier PE's write to a later PE's read.

This is deliberately a byte-addressed adapter: it can be passed directly as
the ``memory_read``/``memory_write`` callbacks used by the embedded workers.
"""

from __future__ import annotations

from collections.abc import Iterable

from .host_memory import HostMemoryBridge
from .memory import GuestRegion, MemoryAccessError, MemorySnapshot


class ParallelMemoryConflict(RuntimeError):
    """Raised when a parallel round cannot be committed safely."""

    def __init__(
        self,
        message: str,
        *,
        writer_pe_id: int | None = None,
        reader_pe_id: int | None = None,
        addresses: Iterable[int] = (),
    ):
        self.writer_pe_id = writer_pe_id
        self.reader_pe_id = reader_pe_id
        self.addresses = tuple(sorted(set(addresses)))
        super().__init__(message)


class ParallelMemoryView:
    """One PE's isolated read/write view of a round snapshot."""

    def __init__(self, coordinator: "ParallelMemoryCoordinator", pe_id: int):
        self._coordinator = coordinator
        self.pe_id = pe_id
        self._reads: set[int] = set()
        self._writes: dict[int, int] = {}
        self._closed = False

    @property
    def read_addresses(self) -> frozenset[int]:
        """Addresses read by this PE, including reads satisfied by own writes."""

        return frozenset(self._reads)

    @property
    def write_addresses(self) -> frozenset[int]:
        """Addresses written by this PE."""

        return frozenset(self._writes)

    @property
    def buffered_writes(self) -> tuple[tuple[int, int], ...]:
        """The final buffered byte at each address, in address order."""

        return tuple(sorted(self._writes.items()))

    def read_byte(self, address: int) -> int:
        self._check_open()
        value = self._coordinator._read_snapshot_byte(address)
        self._reads.add(address)
        return self._writes.get(address, value)

    def read_chunk(self, address: int, size: int) -> bytes:
        if size <= 0:
            raise ValueError("memory chunk size must be positive")
        size = self._coordinator._readable_chunk_size(address, size)
        return bytes(self.read_byte(address + offset) for offset in range(size))

    def write_byte(self, address: int, value: int) -> None:
        self._check_open()
        if not 0 <= value <= 0xFF:
            raise ValueError("memory value must fit in one byte")
        self._coordinator._check_access(address, "w")
        self._writes[address] = value

    def write_chunk(self, address: int, data: bytes | bytearray | memoryview) -> None:
        payload = bytes(data)
        for offset, value in enumerate(payload):
            self.write_byte(address + offset, value)

    def _close(self, *, discard: bool) -> None:
        if discard:
            self._writes.clear()
        self._closed = True

    def _check_open(self) -> None:
        if self._closed or self._coordinator.closed:
            raise RuntimeError("parallel memory view is closed")


class ParallelMemoryCoordinator:
    """Coordinate isolated memory views for a single parallel PE round."""

    def __init__(self, bridge: HostMemoryBridge):
        if not isinstance(bridge, HostMemoryBridge):
            raise TypeError("parallel memory coordinator requires HostMemoryBridge")
        self.bridge = bridge
        self.snapshot: MemorySnapshot = bridge.snapshot()
        self._snapshot_bytes = dict(self.snapshot.bytes_by_address)
        self._regions = self.snapshot.regions
        self._initial_generation = bridge.generation
        self._views: dict[int, ParallelMemoryView] = {}
        self.closed = False

    def view(self, pe_id: int) -> ParallelMemoryView:
        """Create the sole transactional view for ``pe_id`` in this round."""

        self._check_open()
        if not isinstance(pe_id, int) or isinstance(pe_id, bool) or pe_id < 0:
            raise ValueError("PE id must be a non-negative integer")
        if pe_id in self._views:
            raise ValueError(f"PE {pe_id} already has a memory view")
        view = ParallelMemoryView(self, pe_id)
        self._views[pe_id] = view
        return view

    @property
    def views(self) -> tuple[ParallelMemoryView, ...]:
        return tuple(self._views[pe_id] for pe_id in sorted(self._views))

    def commit(self) -> tuple[tuple[int, int, int], ...]:
        """Validate and apply writes in ascending PE order.

        A later PE that read an address written by an earlier PE could have
        observed a different value under sequential PE-order execution.  Such
        a round is rejected before the central bridge is mutated.  Write/write
        overlap is safe: the later PE's byte deterministically wins.
        """

        self._check_open()
        if self.bridge.generation != self._initial_generation:
            raise ParallelMemoryConflict(
                "host memory changed while the parallel round was active"
            )

        prior_writers: dict[int, int] = {}
        for view in self.views:
            conflicts = set(prior_writers).intersection(view.read_addresses)
            if conflicts:
                writer_pe_id = min(prior_writers[address] for address in conflicts)
                writer_addresses = {
                    address
                    for address in conflicts
                    if prior_writers[address] == writer_pe_id
                }
                raise ParallelMemoryConflict(
                    f"PE {writer_pe_id} writes memory read by later PE {view.pe_id}",
                    writer_pe_id=writer_pe_id,
                    reader_pe_id=view.pe_id,
                    addresses=writer_addresses,
                )
            for address in view.write_addresses:
                prior_writers.setdefault(address, view.pe_id)

        committed: list[tuple[int, int, int]] = []
        for view in self.views:
            for address, value in view.buffered_writes:
                self.bridge.write_byte(address, value)
                committed.append((view.pe_id, address, value))
        for view in self._views.values():
            view._close(discard=False)
        self.closed = True
        return tuple(committed)

    def rollback(self) -> None:
        """Discard every buffered write without changing central memory."""

        self._check_open()
        for view in self._views.values():
            view._close(discard=True)
        self.closed = True

    def _read_snapshot_byte(self, address: int) -> int:
        self._check_access(address, "r")
        return self._snapshot_bytes.get(address, 0)

    def _readable_chunk_size(self, address: int, size: int) -> int:
        region = self._check_access(address, "r")
        if region is not None:
            return min(size, region.end - address)
        page_end = (address & ~0xFFF) + 0x1000
        next_region = min(
            (region.base for region in self._regions if region.base > address),
            default=page_end,
        )
        return min(size, max(1, min(page_end, next_region) - address))

    def _check_access(self, address: int, permission: str) -> GuestRegion | None:
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            raise MemoryAccessError(permission, address, 1, "invalid address range")
        for region in self._regions:
            if region.base <= address < region.end:
                if permission not in region.permissions:
                    raise MemoryAccessError(permission, address, 1, "permission denied")
                return region
        if self.bridge.unmapped_policy == "zero":
            return None
        raise MemoryAccessError(permission, address, 1, "unmapped address")

    def _check_open(self) -> None:
        if self.closed:
            raise RuntimeError("parallel memory round is closed")


__all__ = [
    "ParallelMemoryConflict",
    "ParallelMemoryCoordinator",
    "ParallelMemoryView",
]
