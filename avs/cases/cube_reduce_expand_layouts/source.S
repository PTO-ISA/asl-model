.section .text,"ax"
.globl _start
_start:
  # Results occupy 16 bytes; asymmetric M16 and M32 inputs follow them.
  addi sp, 16, ->a0
  addi sp, 24, ->a1
  addi zero, 10, ->a2
  addi zero, 20, ->a3
  addi sp, 8, ->a4

  # Load M16 [1,2] and reduce it to row sum 3.
  BSTART.TLOAD U32
  B.DATR ND2M16, DTYPE_NONE, Zero
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->t<128B>
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  BSTART.SFU TROWSUM, U32
  # B.DATR direct Local CUBE_M16 (Layout=31), DTYPE_NONE, Zero.
  .byte 0xa3,0x1f,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT t#1, mask=1111, last, ->u<128B>
  C.BSTOP

  # Fill M16 [10,10], then add the broadcast row sum.
  BSTART.VEC TEXPANDS, U32
  .byte 0xa3,0x1f,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->n<128B>
  B.IOR [a2, zero, zero], ->zero
  C.BSTOP
  BSTART.SFU TROWEXPANDADD, U32
  .byte 0xa3,0x1f,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT n#1, u#1, mask=1111, last, ->m<128B>
  C.BSTOP
  BSTART.TSTORE U32
  B.DATR M162ND, DTYPE_NONE, Null
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT m#1, mask=1111, last
  B.IOR [sp, zero, zero], ->zero
  C.BSTOP

  # Load M32 [5,9] and reduce it to row sum 14.
  BSTART.TLOAD U32
  B.DATR ND2M32, DTYPE_NONE, Zero
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->t<256B>
  B.IOR [a1, zero, zero], ->zero
  C.BSTOP
  BSTART.SFU TROWSUM, U32
  # B.DATR direct Local CUBE_M32 (Layout=29), DTYPE_NONE, Zero.
  .byte 0xa3,0x1e,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT t#1, mask=1111, last, ->u<128B>
  C.BSTOP

  # Fill M32 [20,20], then add the broadcast row sum.
  BSTART.VEC TEXPANDS, U32
  .byte 0xa3,0x1e,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT mask=1111, last, ->n<256B>
  B.IOR [a3, zero, zero], ->zero
  C.BSTOP
  BSTART.SFU TROWEXPANDADD, U32
  .byte 0xa3,0x1e,0xf0,0x01
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT n#1, u#1, mask=1111, last, ->m<256B>
  C.BSTOP
  BSTART.TSTORE U32
  B.DATR M322ND, DTYPE_NONE, Null
  C.B.DIMI 2, ->lb0
  C.B.DIMI 1, ->lb1
  B.IOT m#1, mask=1111, last
  B.IOR [a4, zero, zero], ->zero
  C.BSTOP
.globl cross_model_stop
cross_model_stop:
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result: .zero 16
.size cross_model_result,16
.section .result.inputs,"aw"
.balign 4
  .long 1, 2
  .long 5, 9
.globl cross_model_result_size
.set cross_model_result_size,16
