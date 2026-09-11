"""Runtime address-space layout for ASL-backed ELF execution.

The layout is deliberately independent of instruction semantics.  It answers
one host-runtime question: where should the optional PE stacks live relative
to the loaded image?
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import ProgramImage


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass(frozen=True)
class StackLayout:
    """One downward-growing stack bank."""

    pe_id: int
    base: int
    size: int
    top: int
    red_zone: int = 0

    @property
    def stack_pointer(self) -> int:
        return self.top - self.red_zone

    @property
    def end(self) -> int:
        return self.base + self.size


@dataclass(frozen=True)
class RuntimeLayout:
    """Resolved guest layout, including all mapped stack banks."""

    policy: str
    image_end: int
    image_start: int
    pe_count: int
    stack_size: int
    stack_gap: int
    stack_stride: int
    red_zone: int
    stacks: tuple[StackLayout, ...] = ()

    @property
    def stack_top(self) -> int | None:
        return self.stacks[0].top if self.stacks else None

    def stack_pointer_for(self, pe_id: int = 0) -> int:
        if not 0 <= pe_id < len(self.stacks):
            raise ValueError("stack bank is not mapped")
        return self.stacks[pe_id].stack_pointer

    @classmethod
    def resolve(
        cls,
        image: ProgramImage,
        *,
        policy: str = "after-image",
        stack_top: int | None = None,
        stack_size: int = 0x01000000,
        stack_gap: int = 0x1000,
        stack_stride: int | None = None,
        red_zone: int = 0,
        pe_count: int = 1,
        page_size: int = 4096,
        address_bits: int = 64,
    ) -> "RuntimeLayout":
        if policy not in {"explicit", "after-image", "disabled"}:
            raise ValueError("stack_policy must be 'explicit', 'after-image', or 'disabled'")
        if pe_count <= 0:
            raise ValueError("pe_count must be positive")
        if page_size <= 0 or page_size & (page_size - 1):
            raise ValueError("page_size must be a positive power of two")
        if stack_size == 0 and policy == "after-image":
            stack_size = 0x01000000
        if stack_size <= 0 and policy != "disabled":
            raise ValueError("stack_size must be positive")
        if stack_gap < 0 or red_zone < 0:
            raise ValueError("stack_gap and red_zone cannot be negative")
        if red_zone >= stack_size and policy != "disabled":
            raise ValueError("red_zone must be smaller than stack_size")
        image_end = max(segment.address + segment.memory_size for segment in image.segments)
        # Keep the established PE-bank ABI (adjacent banks) unless callers
        # explicitly request a larger stride.  ``stack_gap`` reserves space
        # below the first bank; ``stack_stride`` controls inter-bank spacing.
        stride = stack_stride if stack_stride is not None else stack_size
        if policy == "disabled":
            return cls(policy, image_end, min(s.address for s in image.segments), pe_count, 0, stack_gap, 0, 0, ())
        if stride < stack_size:
            raise ValueError("stack_stride must be at least stack_size")
        if stack_top is None:
            if policy == "explicit":
                raise ValueError("explicit stack_policy requires stack_top")
            # The first stack grows downward.  Leave gap bytes between the
            # highest PT_LOAD byte and its lower boundary.
            stack_top = _align_up(image_end + stack_gap + stack_size, page_size)
        if stack_top <= 0:
            raise ValueError("stack_top must be positive")
        limit = 1 << address_bits
        stacks = tuple(
            StackLayout(i, stack_top - stack_size + i * stride,
                        stack_size, stack_top + i * stride, red_zone)
            for i in range(pe_count)
        )
        for stack in stacks:
            if stack.base < 0 or stack.end > limit or stack.top > limit:
                raise ValueError("stack layout exceeds guest address range")
            if any(stack.base < s.address + s.memory_size and stack.end > s.address for s in image.segments):
                raise ValueError("stack mapping overlaps PT_LOAD image")
        for left, right in zip(stacks, stacks[1:]):
            if left.end > right.base:
                raise ValueError("PE stack mappings overlap")
        return cls(policy, image_end, min(s.address for s in image.segments), pe_count, stack_size, stack_gap,
                   stride, red_zone, stacks)


__all__ = ["RuntimeLayout", "StackLayout"]
