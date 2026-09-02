#include "pto/pto_asl_model.h"

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <iostream>

namespace {

bool ParseInteger(const char *text, std::uint64_t *value)
{
    errno = 0;
    char *end = nullptr;
    const unsigned long long parsed = std::strtoull(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0') {
        return false;
    }
    *value = static_cast<std::uint64_t>(parsed);
    return true;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc != 19) {
        std::cerr
            << "usage: pto-model-run RUNNER ASL_SPEC ASLREF LOCK ELF SIDECAR MANIFEST "
               "RESULT STOP_PC MAX_STEPS RESULT_ADDRESS RESULT_SIZE STACK_TOP "
               "MEMORY_BYTES TILE_ELEMENTS STOP_AFTER_HITS START_PC RETURN_PC\n";
        return 2;
    }
    pto_model_elf_run_config_t config{};
    config.abi_version = PTO_ASL_MODEL_ABI_VERSION;
    config.struct_size = sizeof(config);
    config.runner_path = argv[1];
    config.asl_spec_path = argv[2];
    config.aslref_path = argv[3];
    config.lock_path = argv[4];
    config.elf_path = argv[5];
    config.sidecar_path = argv[6];
    config.manifest_output_path = argv[7];
    config.result_output_path = argv[8];
    if (!ParseInteger(argv[9], &config.stop_pc) ||
        !ParseInteger(argv[10], &config.max_steps) ||
        !ParseInteger(argv[11], &config.result_address) ||
        !ParseInteger(argv[12], &config.result_size) ||
        !ParseInteger(argv[13], &config.stack_top) ||
        !ParseInteger(argv[14], &config.memory_bytes) ||
        !ParseInteger(argv[15], &config.tile_elements) ||
        !ParseInteger(argv[16], &config.stop_after_hits) ||
        !ParseInteger(argv[17], &config.start_pc) ||
        !ParseInteger(argv[18], &config.return_pc)) {
        std::cerr << "invalid integer argument\n";
        return 2;
    }
    const pto_model_status_t status = pto_model_run_elf(&config);
    if (status != PTO_MODEL_STATUS_OK) {
        std::cerr << "PTO ASL model failed with status " << status << '\n';
        return 1;
    }
    return 0;
}
