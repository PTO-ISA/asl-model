.section .text,"ax"
.globl _start
_start:
  # The source begins after the 32-byte result buffer, followed by two
  # independently initialized parameter words. ParamGPR2 is the zero GPR.
  addi sp, 32, ->a0
  addi sp, 64, ->a1
  # LD [a1, zero], ->a2/a3. Raw bytes keep the case independent from
  # assembler spelling while retaining the canonical PTO LD encodings.
  .byte 0x09,0xb2,0x01,0x00
  addi a1, 8, ->a1
  .byte 0x89,0xb2,0x01,0x00

  # Canonical BSTART.TIMG2COL U8 encoding. The current compiler identity
  # covers the form while the raw spelling avoids requiring an assembler alias.
  .byte 0x81,0x11,0xc1,0xd9
  C.B.DIMI 32, ->lb0
  C.B.DIMI 1, ->lb1
  C.B.DIMI 32, ->lb2
  B.IOR [a0, zero, zero], ->zero
  B.IOR [a2, a3, zero], ->zero
  B.IOS mask=1000, ->S8<128B>
  C.BSTOP

  # Store the published singleton Shared ND result and compare the exact
  # 32-byte logical row with the independent golden vector.
  BSTART.TSTORE U8
  B.IOS S8, mask=1000
  B.IOR [sp, zero, zero], ->zero
  C.BSTOP
.globl cross_model_stop
cross_model_stop:
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .zero 32
.size cross_model_result,32
.section .result.inputs,"aw"
  .byte 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16
  .byte 17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32
.balign 8
  .quad 0x0101002000010001
  .quad 0x0001042100000000
.globl cross_model_result_size
.set cross_model_result_size,32
