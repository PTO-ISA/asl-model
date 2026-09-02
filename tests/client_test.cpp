#include "pto/pto_asl_model.h"

namespace {

pto_model_elf_run_config_t ValidConfig(const char *runner)
{
    pto_model_elf_run_config_t config{};
    config.abi_version = PTO_ASL_MODEL_ABI_VERSION;
    config.struct_size = sizeof(config);
    config.runner_path = runner;
    config.asl_spec_path = "spec.asl";
    config.aslref_path = "aslref";
    config.elf_path = "case.elf";
    config.sidecar_path = nullptr;
    config.manifest_output_path = "manifest.json";
    config.stop_pc = 4;
    config.max_steps = 1;
    config.memory_bytes = 65536;
    config.tile_elements = 32768;
    config.stop_after_hits = 1;
    config.start_pc = 0;
    config.return_pc = 0;
    return config;
}

} // namespace

int main()
{
    if (pto_model_run_elf(nullptr) != PTO_MODEL_STATUS_INVALID_ARGUMENT) {
        return 1;
    }

    auto mismatch = ValidConfig("/usr/bin/true");
    mismatch.abi_version = 0;
    if (pto_model_run_elf(&mismatch) != PTO_MODEL_STATUS_ABI_MISMATCH) {
        return 2;
    }

    auto success = ValidConfig("/usr/bin/true");
    if (pto_model_run_elf(&success) != PTO_MODEL_STATUS_OK) {
        return 3;
    }

    auto failure = ValidConfig("/usr/bin/false");
    if (pto_model_run_elf(&failure) != PTO_MODEL_STATUS_WORKER_FAILED) {
        return 4;
    }

    auto missing = ValidConfig("/path/that/does/not/exist");
    if (pto_model_run_elf(&missing) != PTO_MODEL_STATUS_WORKER_LAUNCH_ERROR) {
        return 5;
    }
    return 0;
}
