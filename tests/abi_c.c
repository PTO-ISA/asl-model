#include "pto/pto_asl_model.h"

int main(void)
{
    pto_model_elf_run_config_t config = {0};
    config.abi_version = PTO_ASL_MODEL_ABI_VERSION;
    config.struct_size = sizeof(config);
    return config.abi_version == 0 ? 1 : 0;
}
