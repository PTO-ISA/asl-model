.section .text,"ax"
.globl _start
_start:
  # BSTART followed by the sole accepted 64-bit form, L.BSTOP.
  .byte 0x11,0x00,0x00,0x00
  .byte 0x0f,0x00,0x00,0x00,0x01,0x00,0x00,0x00
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .byte 0xa5,0x5a,0x00,0x00
.size cross_model_result,4
.globl cross_model_result_size
.set cross_model_result_size,4
