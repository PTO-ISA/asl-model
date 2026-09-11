#include "pto/pto_asl_model.h"

#include <cstdlib>
#include <fstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

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

std::vector<std::string> ReadLines(const char *path)
{
    std::ifstream input(path);
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(input, line)) {
        lines.push_back(line);
    }
    return lines;
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

    char runner_path[] = "/tmp/pto-asl-model-runner-XXXXXX";
    const int runner_fd = mkstemp(runner_path);
    if (runner_fd < 0) {
        return 6;
    }
    close(runner_fd);
    {
        std::ofstream runner(runner_path);
        runner << "#!/bin/sh\n"
               << "printf '%s\\n' \"$@\" > \"$PTO_ASL_MODEL_ARGV_OUTPUT\"\n";
    }
    if (chmod(runner_path, 0700) != 0) {
        return 7;
    }
    char output_path[] = "/tmp/pto-asl-model-argv-XXXXXX";
    const int output_fd = mkstemp(output_path);
    if (output_fd < 0) {
        return 8;
    }
    close(output_fd);
    if (setenv("PTO_ASL_MODEL_ARGV_OUTPUT", output_path, 1) != 0) {
        return 9;
    }

    auto routed = ValidConfig(runner_path);
    routed.lock_path = "lock.json";
    routed.sidecar_path = "case.sidecar.json";
    routed.result_output_path = "result.bin";
    routed.stop_pc = 4;
    routed.stop_after_hits = 2;
    routed.start_pc = 8;
    routed.return_pc = 12;
    routed.max_steps = 3;
    routed.result_address = 16;
    routed.result_size = 20;
    routed.stack_top = 24;
    routed.memory_bytes = 65536;
    routed.tile_elements = 32;
    if (pto_model_run_elf(&routed) != PTO_MODEL_STATUS_OK) {
        return 10;
    }
    const std::vector<std::string> expected = {
        "--asl-spec", "spec.asl", "--aslref", "aslref",
        "--elf", "case.elf", "--stop-pc", "4",
        "--stop-after-hits", "2", "--start-pc", "8",
        "--return-pc", "12", "--max-steps", "3",
        "--result-address", "16", "--result-size", "20",
        "--stack-top", "24", "--memory-bytes", "65536",
        "--tile-elements", "32", "--manifest-out", "manifest.json",
        "--quiet", "--lock", "lock.json", "--sidecar", "case.sidecar.json",
        "--result-out", "result.bin",
    };
    if (ReadLines(output_path) != expected) {
        return 11;
    }
    unsetenv("PTO_ASL_MODEL_ARGV_OUTPUT");
    unlink(output_path);
    unlink(runner_path);
    return 0;
}
