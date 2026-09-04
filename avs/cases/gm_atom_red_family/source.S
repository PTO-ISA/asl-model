.macro LOAD_U32 destination, address
  BSTART.TLOAD U32
  C.B.DIMI 1, ->lb0
  B.IOT mask=1111, last, ->\destination<128B>
  B.IOR [\address, zero, zero], ->zero
  C.BSTOP
.endm

.macro STORE_OLD source
  BSTART.TSTORE U32
  C.B.DIMI 1, ->lb0
  B.IOT \source#1, mask=1111, last
  B.IOR [a2, zero, zero], ->zero
  C.BSTOP
  addi a2, 4, ->a2
.endm

.macro RUN_MGATHER operation
  LOAD_U32 u, a1
  BSTART.MGATHER.\operation U32
  C.B.DIMI 1, ->lb0
  B.IOT t#1, u#1, mask=1111, last, ->n<128B>
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  STORE_OLD n
  addi a0, 4, ->a0
  addi a1, 4, ->a1
.endm

.macro RUN_MSCATTER operation
  LOAD_U32 u, a1
  BSTART.MSCATTER.\operation U32
  C.B.DIMI 1, ->lb0
  B.IOT t#1, u#1, mask=1111, last
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  addi a0, 4, ->a0
  addi a1, 4, ->a1
.endm

.macro RUN_MSCATTER_M operation
  LOAD_U32 m, a1
  BSTART.MSCATTER.\operation U32
  C.B.DIMI 1, ->lb0
  B.IOT t#1, m#1, mask=1111, last
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  addi a0, 4, ->a0
  addi a1, 4, ->a1
.endm

.section .text,"ax"
.globl _start
_start:
  # Result = nineteen final GM words followed by ten MGATHER observed-old words.
  addi sp, 0, ->a0
  addi sp, 120, ->a1
  addi sp, 76, ->a2
  addi sp, 116, ->a3
  addi zero, 1, ->a5
  LOAD_U32 t, a3

  RUN_MGATHER ADD
  RUN_MGATHER AND

  # CAS consumes separate expected and replacement inputs.
  LOAD_U32 u, a1
  addi a1, 4, ->a4
  LOAD_U32 m, a4
  BSTART.MGATHER.CAS U32
  C.B.DIMI 1, ->lb0
  B.IOT t#1, u#1, mask=1111
  B.IOT m#1, mask=1111, last, ->n<128B>
  B.IOR [a0, a5, zero], ->zero
  C.BSTOP
  STORE_OLD n
  addi a0, 4, ->a0
  addi a1, 8, ->a1

  RUN_MGATHER DEC
  RUN_MGATHER EXCH
  RUN_MGATHER INC
  RUN_MGATHER MAX
  RUN_MGATHER MIN
  RUN_MGATHER OR
  RUN_MGATHER XOR

  RUN_MSCATTER ADD
  RUN_MSCATTER AND
  RUN_MSCATTER DEC
  RUN_MSCATTER INC
  RUN_MSCATTER MAX
  RUN_MSCATTER MIN
  RUN_MSCATTER_M OR
  # Accepted MSCATTER.POPC semantics carry only the index Tile.
  BSTART.MSCATTER.POPC U32
  C.B.DIMI 1, ->lb0
  B.IOT t#1, mask=1111, last
  B.IOR [a0, zero, zero], ->zero
  C.BSTOP
  addi a0, 4, ->a0
  addi a1, 4, ->a1
  RUN_MSCATTER_M XOR
.globl cross_model_stop
cross_model_stop:
.section .result,"aw"
.globl cross_model_result
.type cross_model_result,@object
cross_model_result:
  # Unique one-request GM locations; closure compares their final values.
  .long 3, 3, 3, 3, 3, 3, 3, 3, 3, 3
  .long 3, 3, 3, 3, 3, 3, 3, 3, 3
  # MGATHER observed-old result area.
  .zero 40
.size cross_model_result,116
.section .result.inputs,"aw"
.balign 4
  # One shared zero byte-displacement index.
  .long 0
  # Per-operation values; CAS contributes expected=3 and replacement=9.
  .long 9, 0xf0, 3, 9, 5, 9, 5, 5, 5, 0xf0, 0xf0
  .long 5, 5, 5, 5, 5, 5, 5, 0, 5
.globl cross_model_result_size
.set cross_model_result_size,116
