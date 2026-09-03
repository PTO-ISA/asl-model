.section .text,"ax"
.globl _start
_start:
  # C.MOVI 7, ->a0; ADDI zero, 94, ->a7; BSTART.SYS FALL; ACRC type 1.
  .byte 0xd6,0x11
  .byte 0x95,0x04,0xe0,0x05
  .byte 0x81,0x10,0x00,0x00
  .byte 0x2b,0x30,0x10,0x00
.globl cross_model_stop
cross_model_stop:
  .byte 0x00,0x00
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .byte 0xe7,0x01,0x00,0x00
.size cross_model_result,4
.globl cross_model_result_size
.set cross_model_result_size,4
