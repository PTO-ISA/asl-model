.section .text,"ax"
.globl _start
_start:
  # The result occupies 12 bytes. Source0 and source1 follow it as three
  # FP32 values each. Their asymmetric infinities make operand reversal
  # observable without deriving the golden from model execution.
  addi sp, 12, ->a0
  addi sp, 24, ->a1

  BSTART.TLOAD FP32
  C.B.DIMI 3, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->t<128B>
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP

  BSTART.TLOAD FP32
  C.B.DIMI 3, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->u<128B>
  B.IOR [a1, zero, zero], ->zero
  C.BSTOP

  # Canonical BSTART.SFU TEXPDIF, FP32: TEPL mode 0, function 29.
  # Keep the selector encoding explicit so this model case exercises the
  # frozen ISA carrier independently of assembler mnemonic availability.
  .byte 0x81,0x91,0xd1,0x09
  C.B.DIMI 3, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT t#1, u#1, mask=1111, last, ->n<128B>
  C.BSTOP

  BSTART.TSTORE FP32
  C.B.DIMI 3, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT n#1, mask=1111, last
  B.IOR [sp, zero, zero], ->zero
  C.BSTOP
.globl cross_model_stop
cross_model_stop:
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .zero 12
.size cross_model_result,12
.section .result.inputs,"aw"
.balign 4
  # source0 = [+0.0, +inf, -inf]
  .long 0x00000000, 0x7f800000, 0xff800000
  # source1 = [+0.0, +0.0, +0.0]
  .long 0x00000000, 0x00000000, 0x00000000
.globl cross_model_result_size
.set cross_model_result_size,12
