#include "pto/pto_asl_model.h"

#include <stddef.h>

int main(void)
{
    return pto_model_run_elf(NULL) == PTO_MODEL_STATUS_INVALID_ARGUMENT ? 0 : 1;
}
