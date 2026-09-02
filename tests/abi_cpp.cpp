#include "pto/pto_asl_model.h"

#include <type_traits>

static_assert(std::is_standard_layout_v<pto_model_elf_run_config_t>);

int main()
{
    return PTO_ASL_MODEL_ABI_VERSION == 0 ? 1 : 0;
}
