import unittest

from asl_model.runtime.host_memory import HostMemoryBridge, StackImage
from asl_model.runtime.memory import MemoryAccessError
from asl_model.runtime.protocol import ProgramImage, ProgramSegment


class HostMemoryBridgeTest(unittest.TestCase):
    def test_pt_load_bss_is_zero_backed_at_original_address(self):
        image = ProgramImage(
            entry_point=0x4000,
            segments=(ProgramSegment(0x4000, b"AB", 0x100, "rwx", name="load"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image)
        self.assertEqual(bridge.memory.read(0x4000, 2), b"AB")
        self.assertEqual(bridge.memory.read(0x4002, 0xFE), bytes(0xFE))
        self.assertEqual(bridge.memory.regions[0].base, 0x4000)

    def test_stack_is_explicit_and_does_not_relocate_loads(self):
        image = ProgramImage(
            entry_point=0x100000,
            segments=(ProgramSegment(0x100000, b"code", 0x100, "rx"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image, stack_pointer=0x8000, stack_size=0x1000)
        self.assertEqual(bridge.stack, StackImage(0x7000, 0x1000, 0x8000))
        self.assertEqual(bridge.memory.regions[0].base, 0x7000)
        self.assertEqual(bridge.memory.regions[1].base, 0x100000)

    def test_stack_overlap_is_rejected(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"code", 0x100, "rx"),),
        )
        with self.assertRaises(ValueError):
            HostMemoryBridge().load_image(image, stack_pointer=0x1080, stack_size=0x100)

    def test_write_obeys_guest_permissions(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"code", 4, "rx"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image)
        with self.assertRaises(MemoryAccessError):
            bridge.write_byte(0x1000, 7)

    def test_chunk_read_is_clipped_to_one_readable_region(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"abcdef", 6, "rx"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image)

        self.assertEqual(bridge.read_chunk(0x1002, 4096), b"cdef")
        with self.assertRaises(MemoryAccessError):
            bridge.read_chunk(0x1006, 1)

    def test_memory_generation_changes_after_write(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"\0", 4, "rw"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image)
        self.assertEqual(bridge.generation, 0)

        bridge.write_byte(0x1001, 7)

        self.assertEqual(bridge.generation, 1)
        self.assertEqual(bridge.read_chunk(0x1000, 4), b"\0\x07\0\0")

    def test_memory_generation_changes_after_restore(self):
        image = ProgramImage(
            entry_point=0x1000,
            segments=(ProgramSegment(0x1000, b"A", 2, "rw"),),
        )
        bridge = HostMemoryBridge()
        bridge.load_image(image)
        snapshot = bridge.snapshot()
        bridge.write_byte(0x1000, ord("B"))
        generation = bridge.generation

        bridge.restore(snapshot)

        self.assertGreater(bridge.generation, generation)
        self.assertEqual(bridge.read_byte(0x1000), ord("A"))

    def test_zero_policy_maps_page_hole_around_unaligned_load(self):
        image = ProgramImage(
            entry_point=0x13DC8,
            segments=(
                ProgramSegment(
                    0x13DC8,
                    b"code",
                    0x238,
                    "rx",
                    name="unaligned-load",
                ),
            ),
        )
        bridge = HostMemoryBridge(unmapped_policy="zero")
        bridge.load_image(image)

        self.assertEqual(bridge.read_byte(0x13000), 0)
        regions = bridge.memory.regions
        self.assertEqual(
            [(region.base, region.size, region.permissions) for region in regions],
            [
                (0x13000, 0xDC8, "rw"),
                (0x13DC8, 0x238, "rx"),
            ],
        )
        self.assertEqual(bridge.memory.read(0x13DC8, 4), b"code")

    def test_zero_policy_does_not_bypass_existing_permissions(self):
        image = ProgramImage(
            entry_point=0x13DC8,
            segments=(ProgramSegment(0x13DC8, b"code", 0x238, "rx"),),
        )
        bridge = HostMemoryBridge(unmapped_policy="zero")
        bridge.load_image(image)
        with self.assertRaises(MemoryAccessError):
            bridge.write_byte(0x13DC8, 7)


if __name__ == "__main__":
    unittest.main()
