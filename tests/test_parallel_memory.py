import unittest

from asl_model.runtime.host_memory import HostMemoryBridge
from asl_model.runtime.memory import MemoryAccessError
from asl_model.runtime.parallel_memory import (
    ParallelMemoryConflict,
    ParallelMemoryCoordinator,
)
from asl_model.runtime.protocol import ProgramImage, ProgramSegment


def memory_bridge(*, permissions: str = "rw") -> HostMemoryBridge:
    bridge = HostMemoryBridge()
    bridge.load_image(
        ProgramImage(
            entry_point=0x1000,
            segments=(
                ProgramSegment(
                    0x1000,
                    bytes(range(16)),
                    16,
                    permissions,
                    name="data",
                ),
            ),
        )
    )
    return bridge


class ParallelMemoryTest(unittest.TestCase):
    def test_disjoint_parallel_writes_commit_together(self):
        bridge = memory_bridge()
        round_memory = ParallelMemoryCoordinator(bridge)
        pe1 = round_memory.view(1)
        pe0 = round_memory.view(0)

        pe1.write_byte(0x1002, 0x22)
        pe0.write_byte(0x1001, 0x11)

        self.assertEqual(
            round_memory.commit(),
            ((0, 0x1001, 0x11), (1, 0x1002, 0x22)),
        )
        self.assertEqual(bridge.read_chunk(0x1000, 4), b"\x00\x11\x22\x03")

    def test_read_observes_own_buffered_write_and_records_both_sets(self):
        round_memory = ParallelMemoryCoordinator(memory_bridge())
        view = round_memory.view(3)

        view.write_byte(0x1004, 0xA5)

        self.assertEqual(view.read_byte(0x1004), 0xA5)
        self.assertEqual(view.read_addresses, frozenset({0x1004}))
        self.assertEqual(view.write_addresses, frozenset({0x1004}))

    def test_earlier_write_later_read_conflict_does_not_commit(self):
        bridge = memory_bridge()
        round_memory = ParallelMemoryCoordinator(bridge)
        earlier = round_memory.view(0)
        later = round_memory.view(2)
        earlier.write_byte(0x1005, 0xEE)
        self.assertEqual(later.read_byte(0x1005), 5)

        with self.assertRaises(ParallelMemoryConflict) as raised:
            round_memory.commit()

        self.assertEqual(raised.exception.writer_pe_id, 0)
        self.assertEqual(raised.exception.reader_pe_id, 2)
        self.assertEqual(raised.exception.addresses, (0x1005,))
        self.assertEqual(bridge.read_byte(0x1005), 5)

    def test_overlapping_writes_resolve_in_deterministic_pe_order(self):
        bridge = memory_bridge()
        round_memory = ParallelMemoryCoordinator(bridge)
        later = round_memory.view(9)
        earlier = round_memory.view(2)
        later.write_byte(0x1006, 0x99)
        earlier.write_byte(0x1006, 0x22)

        round_memory.commit()

        self.assertEqual(bridge.read_byte(0x1006), 0x99)

    def test_permissions_are_checked_before_buffering(self):
        bridge = memory_bridge(permissions="r")
        round_memory = ParallelMemoryCoordinator(bridge)
        view = round_memory.view(0)

        with self.assertRaises(MemoryAccessError):
            view.write_byte(0x1000, 7)
        self.assertEqual(view.write_addresses, frozenset())
        self.assertEqual(bridge.read_byte(0x1000), 0)

        unreadable = memory_bridge(permissions="w")
        unreadable_view = ParallelMemoryCoordinator(unreadable).view(0)
        with self.assertRaises(MemoryAccessError):
            unreadable_view.read_byte(0x1000)

    def test_chunk_read_stops_before_adjacent_unreadable_region(self):
        bridge = HostMemoryBridge()
        bridge.load_image(
            ProgramImage(
                entry_point=0x1000,
                segments=(
                    ProgramSegment(0x1000, b"AB", 2, "rx"),
                    ProgramSegment(0x1002, b"CD", 2, "w"),
                ),
            )
        )
        view = ParallelMemoryCoordinator(bridge).view(0)

        self.assertEqual(view.read_chunk(0x1000, 4096), b"AB")

    def test_rollback_and_precommit_views_do_not_mutate_central_memory(self):
        bridge = memory_bridge()
        round_memory = ParallelMemoryCoordinator(bridge)
        view = round_memory.view(0)
        view.write_chunk(0x1008, b"XY")

        self.assertEqual(bridge.read_chunk(0x1008, 2), b"\x08\x09")
        self.assertEqual(bridge.generation, 0)

        round_memory.rollback()

        self.assertEqual(bridge.read_chunk(0x1008, 2), b"\x08\x09")
        self.assertEqual(bridge.generation, 0)
        with self.assertRaises(RuntimeError):
            view.read_byte(0x1008)


if __name__ == "__main__":
    unittest.main()
