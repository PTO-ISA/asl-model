import unittest

from asl_model.runtime.config import RuntimeLayout
from asl_model.runtime.protocol import ProgramImage, ProgramSegment


def image():
    return ProgramImage(0x1000, (ProgramSegment(0x1000, b"\0" * 16, 0x1000, "rx"),))


class RuntimeLayoutTests(unittest.TestCase):
    def test_after_image_is_automatic_and_disjoint(self):
        layout = RuntimeLayout.resolve(image(), stack_size=0x2000, stack_gap=0x1000, pe_count=2)
        self.assertEqual(layout.stacks[0].base, 0x3000)
        self.assertLess(0x2000, layout.stacks[0].base)
        self.assertLessEqual(layout.stacks[0].end, layout.stacks[1].base)

    def test_explicit_requires_top(self):
        with self.assertRaises(ValueError):
            RuntimeLayout.resolve(image(), policy="explicit", stack_size=0x1000)

    def test_overlap_is_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeLayout.resolve(image(), policy="explicit", stack_top=0x1800, stack_size=0x1000)

    def test_disabled_has_no_stacks(self):
        layout = RuntimeLayout.resolve(image(), policy="disabled", pe_count=4)
        self.assertEqual(layout.stacks, ())

    def test_stride_overlap_is_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeLayout.resolve(image(), stack_size=0x2000, stack_stride=0x1000, pe_count=2)


if __name__ == "__main__":
    unittest.main()
