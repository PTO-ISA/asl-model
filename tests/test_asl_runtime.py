import unittest

from asl_model.state import ArchitectureState, ExecutionResult, StateEnvelope
from asl_model.runtime import (
    CallbackSemanticBackend,
    ElfLoadRequest,
    GuestMemory,
    InstructionRequest,
    MemoryAccessError,
    MockHostAdapter,
    ProgramImage,
    ProgramSegment,
    RuntimeAdapter,
    RuntimeSnapshot,
    TransactionError,
)


class AslRuntimeContractTest(unittest.TestCase):
    def image(self):
        return ProgramImage(
            entry_point=0x1000,
            stack_pointer=0x8000,
            segments=(
                ProgramSegment(0x1000, b"\x01\x02\x03\x04", 0x100, "rx", name="text"),
                ProgramSegment(0x2000, b"input", 0x100, "rw", name="data"),
            ),
            symbols={"entry": 0x1000},
        )

    def backend(self):
        def execute(request, state, memory):
            state.scalar.registers["x1"] = memory.read_u(0x2000, 1) + 1
            state.scalar.pc = request.pc + len(request.encoding)
            return ExecutionResult(
                instruction=request.mnemonic or "MOCK",
                status="committed",
                initial_state=StateEnvelope.initial(ArchitectureState()),
                final_state=state,
                pc_before=request.pc,
                pc_after=state.scalar.pc,
            )

        return CallbackSemanticBackend(execute)

    def test_load_fetch_and_step_are_backend_neutral(self):
        host = MockHostAdapter(self.image())
        runtime = RuntimeAdapter(host, self.backend(), artifact={"spec": "test"})
        image = runtime.load(ElfLoadRequest(image=b"placeholder"))
        self.assertEqual(image.entry_point, 0x1000)
        request = runtime.fetch()
        self.assertEqual(request.encoding, b"\x01\x02\x03\x04")
        result = runtime.step(request)
        self.assertEqual(result.status, "committed")
        self.assertEqual(runtime.state.scalar.registers["x1"], ord("i") + 1)
        self.assertEqual(runtime.state.scalar.pc, 0x1004)
        self.assertEqual(runtime.instruction_count, 1)

    def test_fetch_is_a_stable_legacy_boundary(self):
        """The compatibility fetch path exposes bytes without decoding them.

        This is the boundary that an ASL-owned ``ExecuteOnePTOStep`` path
        supersedes.  It must remain usable by native backends and old callers:
        the runtime supplies bytes at an explicit PC, while the semantic
        backend remains responsible for interpreting those bytes.
        """
        host = MockHostAdapter(self.image())
        runtime = RuntimeAdapter(host, self.backend())
        runtime.load(ElfLoadRequest(image=b"placeholder"))

        request = runtime.fetch(size=8)
        self.assertEqual(request.pc, 0x1000)
        self.assertEqual(request.encoding, b"\x01\x02\x03\x04\0\0\0\0")
        self.assertEqual(request.pe_id, 0)
        self.assertEqual(request.thread_id, 0)

        explicit = runtime.fetch(0x2000, size=3)
        self.assertEqual(explicit.pc, 0x2000)
        self.assertEqual(explicit.encoding, b"inp")

    def test_fetch_rejects_invalid_sizes_without_semantic_side_effects(self):
        host = MockHostAdapter(self.image())
        runtime = RuntimeAdapter(host, self.backend())
        runtime.load(ElfLoadRequest(image=b"placeholder"))
        for size in (0, -1):
            with self.assertRaises(ValueError):
                runtime.fetch(size=size)
        self.assertEqual(runtime.instruction_count, 0)
        self.assertEqual(runtime.state.scalar.pc, 0x1000)

    def test_snapshot_restores_state_and_memory(self):
        host = MockHostAdapter(self.image())
        runtime = RuntimeAdapter(host, self.backend())
        runtime.load(ElfLoadRequest(image=b"placeholder"))
        checkpoint = runtime.snapshot()
        host.memory.write(0x2000, b"z")
        runtime.reset(ArchitectureState(scalar=runtime.state.scalar))
        # reset only resets architectural state; restore restores both domains.
        runtime.restore(checkpoint)
        self.assertEqual(host.memory.read(0x2000, 5), b"input")
        self.assertEqual(runtime.snapshot().state.state.sha256(), checkpoint.state.state.sha256())
        restored = RuntimeSnapshot.from_dict(checkpoint.as_dict())
        self.assertEqual(restored.memory.as_dict(), checkpoint.memory.as_dict())

    def test_transaction_commit_and_abort(self):
        memory = GuestMemory()
        memory.map_region(0x100, 8, permissions="rw")
        transaction = memory.begin()
        transaction.write(0x100, b"abc")
        self.assertEqual(memory.read(0x100, 3), b"\0\0\0")
        self.assertEqual(transaction.read(0x100, 3), b"abc")
        transaction.abort()
        self.assertEqual(memory.read(0x100, 3), b"\0\0\0")
        with self.assertRaises(RuntimeError):
            transaction.commit()

    def test_memory_permissions_and_boundaries_are_checked(self):
        memory = GuestMemory()
        memory.map_region(0x100, 4, permissions="rx")
        with self.assertRaises(MemoryAccessError):
            memory.write(0x100, b"x")
        with self.assertRaises(MemoryAccessError):
            memory.read(0x103, 2)

    def test_runtime_rejects_wrong_pc_and_nested_transaction(self):
        host = MockHostAdapter(self.image())
        runtime = RuntimeAdapter(host, self.backend())
        runtime.load(ElfLoadRequest(image=b"placeholder"))
        with self.assertRaises(TransactionError):
            runtime.begin(InstructionRequest(0x2000, b"\0"))
        active = runtime.begin(InstructionRequest(0x1000, b"\0"))
        with self.assertRaises(TransactionError):
            runtime.begin(InstructionRequest(0x1000, b"\0"))
        active.abort()


if __name__ == "__main__":
    unittest.main()
