#include "pto/pto_asl_model.h"

#include <cerrno>
#include <cstdint>
#include <spawn.h>
#include <string>
#include <sys/wait.h>
#include <vector>

extern char **environ;

namespace {

bool HasText(const char *value)
{
    return value != nullptr && value[0] != '\0';
}

std::string Decimal(std::uint64_t value)
{
    return std::to_string(value);
}

} // namespace

extern "C" pto_model_status_t pto_model_run_elf(
    const pto_model_elf_run_config_t *config)
{
    if (config == nullptr) {
        return PTO_MODEL_STATUS_INVALID_ARGUMENT;
    }
    if (config->abi_version != PTO_ASL_MODEL_ABI_VERSION ||
        config->struct_size != sizeof(*config)) {
        return PTO_MODEL_STATUS_ABI_MISMATCH;
    }
    if (!HasText(config->runner_path) || !HasText(config->asl_spec_path) ||
        !HasText(config->aslref_path) || !HasText(config->elf_path) ||
        !HasText(config->manifest_output_path) ||
        (config->max_steps == 0 && !HasText(config->sidecar_path))) {
        return PTO_MODEL_STATUS_INVALID_ARGUMENT;
    }

    const std::string stop_pc = Decimal(config->stop_pc);
    const std::string max_steps = Decimal(config->max_steps);
    const std::string result_address = Decimal(config->result_address);
    const std::string result_size = Decimal(config->result_size);
    const std::string stack_top = Decimal(config->stack_top);
    const std::string memory_bytes = Decimal(config->memory_bytes);
    const std::string tile_elements = Decimal(config->tile_elements);
    const std::string stop_after_hits = Decimal(config->stop_after_hits);
    const std::string start_pc = Decimal(config->start_pc);
    const std::string return_pc = Decimal(config->return_pc);
    std::vector<char *> arguments = {
        const_cast<char *>(config->runner_path),
        const_cast<char *>("--asl-spec"),
        const_cast<char *>(config->asl_spec_path),
        const_cast<char *>("--aslref"),
        const_cast<char *>(config->aslref_path),
        const_cast<char *>("--elf"),
        const_cast<char *>(config->elf_path),
        const_cast<char *>("--stop-pc"),
        const_cast<char *>(stop_pc.c_str()),
        const_cast<char *>("--stop-after-hits"),
        const_cast<char *>(stop_after_hits.c_str()),
        const_cast<char *>("--start-pc"),
        const_cast<char *>(start_pc.c_str()),
        const_cast<char *>("--return-pc"),
        const_cast<char *>(return_pc.c_str()),
        const_cast<char *>("--max-steps"),
        const_cast<char *>(max_steps.c_str()),
        const_cast<char *>("--result-address"),
        const_cast<char *>(result_address.c_str()),
        const_cast<char *>("--result-size"),
        const_cast<char *>(result_size.c_str()),
        const_cast<char *>("--stack-top"),
        const_cast<char *>(stack_top.c_str()),
        const_cast<char *>("--memory-bytes"),
        const_cast<char *>(memory_bytes.c_str()),
        const_cast<char *>("--tile-elements"),
        const_cast<char *>(tile_elements.c_str()),
        const_cast<char *>("--manifest-out"),
        const_cast<char *>(config->manifest_output_path),
        const_cast<char *>("--quiet"),
    };
    if (HasText(config->lock_path)) {
        arguments.push_back(const_cast<char *>("--lock"));
        arguments.push_back(const_cast<char *>(config->lock_path));
    }
    if (HasText(config->sidecar_path)) {
        arguments.push_back(const_cast<char *>("--sidecar"));
        arguments.push_back(const_cast<char *>(config->sidecar_path));
    }
    if (HasText(config->result_output_path)) {
        arguments.push_back(const_cast<char *>("--result-out"));
        arguments.push_back(const_cast<char *>(config->result_output_path));
    }
    arguments.push_back(nullptr);

    pid_t process = 0;
    const int spawn_status = posix_spawn(
        &process,
        config->runner_path,
        nullptr,
        nullptr,
        arguments.data(),
        environ);
    if (spawn_status != 0) {
        return PTO_MODEL_STATUS_WORKER_LAUNCH_ERROR;
    }
    int worker_status = 0;
    while (waitpid(process, &worker_status, 0) < 0) {
        if (errno != EINTR) {
            return PTO_MODEL_STATUS_WORKER_FAILED;
        }
    }
    if (!WIFEXITED(worker_status) || WEXITSTATUS(worker_status) != 0) {
        return PTO_MODEL_STATUS_WORKER_FAILED;
    }
    return PTO_MODEL_STATUS_OK;
}
