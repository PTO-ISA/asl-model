ENTRY(_start)
PHDRS
{
  text PT_LOAD FLAGS(5);
  result PT_LOAD FLAGS(6);
}
SECTIONS
{
  . = 0x100;
  .text : { *(.text*) } :text
  .note.pto.isa : { KEEP(*(.note.pto.isa)) } :text
  . = 0xf00;
  .result : { *(.result*) } :result
}
