#include "pto/pto_asl_model.h"

int main()
{
    return pto_model_run_elf(nullptr) == PTO_MODEL_STATUS_INVALID_ARGUMENT ? 0 : 1;
}
