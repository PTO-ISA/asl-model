.section .text,"ax"
.globl _start
_start:
  # A=2.0f, B=3.0f, and explicit C=5.0f live after the result bytes.
  addi sp, 8, ->a0
  addi sp, 12, ->a1
  addi sp, 16, ->a2
  BSTART.TLOAD FP32
  B.DATR ND2M32, DTYPE_NONE, Zero
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->t<128B>
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  BSTART.TLOAD FP32
  B.DATR ND2N8, DTYPE_NONE, Zero
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->u<128B>
  B.IOR [a1, zero, zero], ->zero
  C.BSTOP
  BSTART.TLOAD FP32
  B.DATR ND2M32, DTYPE_NONE, Zero
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->n<128B>
  B.IOR [a2, zero, zero], ->zero
  C.BSTOP

  # Legacy CCTRL=00 final output.
  BSTART.TMATMUL.ACC FP32
  B.DATR NORM, FP32, Zero
  B.FPATR 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  C.B.DIMI 1, ->lb2
  B.IOT n#1, mask=1111
  B.IOT t#1, u#1, mask=1111
  B.IOT mask=1111, last, ->m<128B>
  C.BSTOP
  BSTART.TSTORE FP32
  B.DATR M322ND, DTYPE_NONE, Null
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT m#1, mask=1111, last
  B.IOR [sp, zero, zero], ->zero
  C.BSTOP

  # CCTRL=11 requests input prefetch and raw-output replacement hints.
  BSTART.TMATMUL.ACC FP32
  B.DATR NORM, FP32, Null
  B.FPATR 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  C.B.DIMI 1, ->lb2
  B.IOT n#1, mask=1111
  B.IOT t#1, u#1, mask=1111
  B.IOT mask=1111, last, ->m<128B>
  C.BSTOP
  addi sp, 4, ->a3
  BSTART.TSTORE FP32
  B.DATR M322ND, DTYPE_NONE, Null
  C.B.DIMI 1, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT m#1, mask=1111, last
  B.IOR [a3, zero, zero], ->zero
  C.BSTOP
.globl cross_model_stop
cross_model_stop:
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .zero 8
.size cross_model_result,8
.section .result.inputs,"aw"
.balign 4
  .long 0x40000000
  .long 0x40400000
  .long 0x40a00000
.globl cross_model_result_size
.set cross_model_result_size,8
