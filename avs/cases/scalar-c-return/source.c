volatile unsigned char cross_model_result[5] = {'P', 'A', 'S', 'S', '\n'};

__asm__(".globl cross_model_result_size\n"
        ".set cross_model_result_size, 5\n");

__attribute__((noinline)) void cross_model_stop(void) {
    for (;;) {
    }
}

int main(void) {
    return 0;
}
