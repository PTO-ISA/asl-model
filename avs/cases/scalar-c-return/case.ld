ENTRY(main)
SECTIONS
{
  . = 0x100;
  .text : { *(.text*) }
  .note.pto.isa : { KEEP(*(.note.pto.isa)) }
  . = 0x2000;
  .data : { *(.data*) }
  .bss : { *(.bss*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
