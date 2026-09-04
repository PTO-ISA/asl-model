#ifndef PTO_ASL_MODEL_H
#define PTO_ASL_MODEL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define PTO_ASL_MODEL_API __declspec(dllexport)
#elif defined(__GNUC__) || defined(__clang__)
#define PTO_ASL_MODEL_API __attribute__((visibility("default")))
#else
#define PTO_ASL_MODEL_API
#endif

#define PTO_ASL_MODEL_ABI_VERSION UINT32_C(0x00020000)

typedef uint32_t pto_model_status_t;
enum {
    PTO_MODEL_STATUS_OK = 0,
    PTO_MODEL_STATUS_INVALID_ARGUMENT = 1,
    PTO_MODEL_STATUS_ABI_MISMATCH = 2,
    PTO_MODEL_STATUS_WORKER_LAUNCH_ERROR = 3,
    PTO_MODEL_STATUS_WORKER_FAILED = 4
};

typedef struct {
    uint32_t abi_version;
    uint32_t struct_size;
    const char *runner_path;
    const char *asl_spec_path;
    const char *aslref_path;
    const char *lock_path;
    const char *elf_path;
    const char *sidecar_path;
    const char *manifest_output_path;
    const char *result_output_path;
    uint64_t stop_pc;
    uint64_t max_steps;
    uint64_t result_address;
    uint64_t result_size;
    uint64_t stack_top;
    uint64_t memory_bytes;
    uint64_t tile_elements;
    uint64_t stop_after_hits;
    uint64_t start_pc;
    uint64_t return_pc;
} pto_model_elf_run_config_t;

/* Execute one complete hosted ELF run through the reference backend. The
 * runner transport is implementation-owned; the caller observes only this
 * versioned ABI and the deterministic manifest. */
PTO_ASL_MODEL_API pto_model_status_t pto_model_run_elf(
    const pto_model_elf_run_config_t *config);

#ifdef __cplusplus
}
#endif

#endif
