"""Sparse guest-address-space bridge used by ASL execution workers.

The bridge is intentionally independent of ELF filenames and of the legacy
emulator.  It preserves the addresses returned by the loader exactly.  A
``PT_LOAD`` with ``memory_size > len(data)`` is represented as file bytes plus
zero-backed BSS by :class:`GuestMemory`; no relocation or materialization of a
large zero range is performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .memory import GuestMemory, MemoryAccessError, MemorySnapshot
from .protocol import ElfLoadRequest, ProgramImage
from .config import RuntimeLayout


class MemoryWorker(Protocol):
    def read_memory_byte(self, address: int) -> int: ...

    def write_memory_byte(self, address: int, value: int) -> None: ...


@dataclass(frozen=True)
class StackImage:
    """Explicit stack mapping selected by the host/runtime configuration."""

    base: int
    size: int
    stack_pointer: int
    permissions: str = "rw"
    name: str = "stack"

    def __post_init__(self) -> None:
        if self.base < 0 or self.size <= 0:
            raise ValueError("stack base and size must be positive")
        if not self.base <= self.stack_pointer <= self.base + self.size:
            raise ValueError("stack pointer must lie inside the stack mapping")


class HostMemoryBridge:
    """Host-side memory contract exposed to ASL worker adapters."""

    def __init__(
        self,
        memory: GuestMemory | None = None,
        worker: MemoryWorker | None = None,
        *,
        unmapped_policy: str = "deny",
        page_size: int = 4096,
    ):
        if unmapped_policy not in {"deny", "zero"}:
            raise ValueError("unmapped_policy must be 'deny' or 'zero'")
        if page_size <= 0 or page_size & (page_size - 1):
            raise ValueError("page_size must be a positive power of two")
        self.memory = memory or GuestMemory()
        self.worker = worker
        self.unmapped_policy = unmapped_policy
        self.page_size = page_size
        self.image: ProgramImage | None = None
        self.stack: StackImage | None = None
        self.stacks: tuple[StackImage, ...] = ()
        self._red_zone = 0
        self.generation = 0

    def load_image(
        self,
        image: ProgramImage,
        *,
        stack_pointer: int | None = None,
        stack_size: int = 0,
        stack_permissions: str = "rw",
        stack_count: int = 1,
        stack_stride: int | None = None,
        stack_policy: str = "explicit",
        runtime_layout: RuntimeLayout | None = None,
        stack_gap: int = 0x1000,
        red_zone: int = 0,
        pe_count: int | None = None,
    ) -> ProgramImage:
        """Map PT_LOAD and optional stack regions using their original VAs."""

        if not isinstance(image, ProgramImage):
            raise TypeError("load_image expects ProgramImage")
        if stack_size < 0:
            raise ValueError("stack_size cannot be negative")
        if stack_count <= 0:
            raise ValueError("stack_count must be positive")
        self.memory = GuestMemory()
        self.generation = 0
        for segment in sorted(image.segments, key=lambda item: item.address):
            self.memory.map_region(
                segment.address,
                segment.memory_size,
                permissions=segment.permissions,
                name=segment.name,
                data=segment.data,
            )
        self.stack = None
        self.stacks = ()
        if runtime_layout is None:
            count = stack_count if pe_count is None else pe_count
            effective_stack_pointer = (
                stack_pointer if stack_pointer is not None else image.stack_pointer
            )
            if stack_size == 0 and stack_policy == "explicit":
                stack_policy = "disabled"
            runtime_layout = RuntimeLayout.resolve(
                image,
                policy=stack_policy,
                stack_top=effective_stack_pointer,
                stack_size=stack_size,
                stack_gap=stack_gap,
                stack_stride=stack_stride,
                red_zone=red_zone,
                pe_count=count,
                page_size=self.page_size,
            )
        self._red_zone = runtime_layout.red_zone
        if runtime_layout.stacks:
            stacks = []
            for layout in runtime_layout.stacks:
                index = layout.pe_id
                name = "stack" if len(runtime_layout.stacks) == 1 else f"stack[{index}]"
                pointer = layout.top
                base = layout.base
                stack = StackImage(base, layout.size, pointer, stack_permissions, name)
                self.memory.map_region(
                    base,
                    layout.size,
                    permissions=stack_permissions,
                    name=stack.name,
                )
                stacks.append(stack)
            self.stacks = tuple(stacks)
            self.stack = self.stacks[0]
        elif stack_pointer is not None or image.stack_pointer is not None:
            # A pointer without a mapping is not a usable stack and should be
            # visible to callers instead of silently allocating at a guessed VA.
            self.stack = None
        self.image = image
        return image

    def stack_pointer_for(self, index: int = 0, *, red_zone: int | None = None) -> int:
        """Return the ABI SP value for a mapped stack bank."""

        if not 0 <= index < len(self.stacks):
            raise ValueError("stack bank is not mapped")
        red_zone = self._red_zone if red_zone is None else red_zone
        if red_zone < 0 or red_zone > self.stacks[index].size:
            raise ValueError("invalid stack red zone")
        return self.stacks[index].stack_pointer - red_zone

    def load_request(
        self,
        loader,
        request: ElfLoadRequest,
        **kwargs,
    ) -> ProgramImage:
        """Load through an injected ELF loader, then map the returned image."""

        image = loader.load(request)
        return self.load_image(image, **kwargs)

    def read_byte(self, address: int) -> int:
        try:
            value = self.memory.read(address, 1)[0]
        except MemoryAccessError:
            if self.unmapped_policy != "zero":
                raise
            self._map_zero_page(address)
            value = 0
        if self.worker is not None:
            worker_value = self.worker.read_memory_byte(address)
            if worker_value != value:
                raise ValueError(
                    f"ASL worker memory mismatch at 0x{address:x}: "
                    f"bridge={value}, worker={worker_value}"
                )
        return value

    def read_chunk(self, address: int, size: int) -> bytes:
        """Read forward within one mapped readable region."""

        if size <= 0:
            raise ValueError("memory chunk size must be positive")
        if self.worker is not None:
            return bytes(self.read_byte(address + offset) for offset in range(size))
        try:
            self.memory.read(address, 1)
        except MemoryAccessError:
            if self.unmapped_policy != "zero":
                raise
            self._map_zero_page(address)
        region = next(
            (
                item
                for item in self.memory.regions
                if item.base <= address < item.end and "r" in item.permissions
            ),
            None,
        )
        if region is None:
            raise MemoryAccessError("r", address, size, "permission denied")
        return self.memory.read(address, min(size, region.end - address))

    def write_byte(self, address: int, value: int) -> None:
        if not 0 <= value <= 0xFF:
            raise ValueError("memory value must fit in one byte")
        try:
            self.memory.write(address, bytes((value,)))
        except MemoryAccessError:
            if self.unmapped_policy != "zero":
                raise
            self._map_zero_page(address)
            self.memory.write(address, bytes((value,)))
        self.generation += 1
        if self.worker is not None:
            self.worker.write_memory_byte(address, value)

    def snapshot(self) -> MemorySnapshot:
        return self.memory.snapshot()

    def restore(self, snapshot: MemorySnapshot) -> None:
        self.memory.restore(snapshot)
        self.generation += 1

    def _map_zero_page(self, address: int) -> None:
        """Map only the unmapped holes in the page containing ``address``.

        ELF PT_LOAD addresses are not required to be page aligned.  Mapping
        the complete page for a zero-policy access would therefore overlap a
        neighboring load segment and, more importantly, would make it
        tempting to replace that segment's permissions.  Split the page at
        every existing region and add zero-backed mappings only for the
        uncovered intervals.  An access inside an existing region is still a
        normal permission check and must remain a fault.
        """
        base = address & ~(self.page_size - 1)
        end = base + self.page_size
        regions = sorted(self.memory.regions, key=lambda region: region.base)

        # Do not turn a permission fault in an existing PT_LOAD/stack mapping
        # into a writable zero page.  The caller's original access kind is
        # not needed here: the bridge only calls this for a one-byte access,
        # and preserving the existing mapping is the important contract.
        for region in regions:
            if region.base <= address < region.end:
                raise MemoryAccessError(
                    "rw", address, 1, "address overlaps an existing mapping"
                )

        cursor = base
        for region in regions:
            if region.end <= base:
                continue
            if region.base >= end:
                break
            hole_end = min(region.base, end)
            if cursor < hole_end:
                self.memory.map_region(
                    cursor,
                    hole_end - cursor,
                    permissions="rw",
                    name="runtime-zero-page",
                )
            cursor = max(cursor, min(region.end, end))
        if cursor < end:
            self.memory.map_region(
                cursor,
                end - cursor,
                permissions="rw",
                name="runtime-zero-page",
            )


__all__ = ["HostMemoryBridge", "MemoryWorker", "StackImage"]
