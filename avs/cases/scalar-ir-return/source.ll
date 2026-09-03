target triple = "linx64-unknown-none-elf"

@cross_model_result = global [5 x i8] c"PASS\0A", align 1
module asm ".globl cross_model_result_size"
module asm ".set cross_model_result_size, 5"

define i32 @main() {
entry:
  ret i32 0
}

define void @cross_model_stop() {
entry:
  br label %loop

loop:
  br label %loop
}
