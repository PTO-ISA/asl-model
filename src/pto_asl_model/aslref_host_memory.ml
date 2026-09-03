open Asllib

module Memory = struct
  let bytes : (int, int) Hashtbl.t = Hashtbl.create 16384

  let integer = function
    | Native.NV_Literal (AST.L_BitVector value) ->
        Z.to_int (Bitvector.to_z_unsigned value)
    | Native.NV_Literal (AST.L_Int value) -> Z.to_int value
    | _ -> invalid_arg "physical memory value must be an integer"

  let read address = Option.value (Hashtbl.find_opt bytes address) ~default:0

  let write address value =
    if address < 0 then invalid_arg "negative physical memory address";
    if value < 0 || value > 255 then
      invalid_arg "physical memory value is not a byte";
    if value = 0 then Hashtbl.remove bytes address
    else Hashtbl.replace bytes address value
end

let named_ty name = AST.T_Named name |> ASTUtils.add_dummy_pos

let primitive_decl ?returns ?(side_effecting = false) name args =
  let open AST in
  {
    name;
    parameters = [];
    args;
    body = SB_Primitive side_effecting;
    return_type = returns;
    subprogram_type =
      (match returns with None -> ST_Procedure | Some _ -> ST_Function);
    recurse_limit = None;
    qualifier = if side_effecting then None else Some Pure;
    override = None;
    builtin = true;
  }

module HostBackend = struct
  include Native.DeterministicBackend

  let read_physical_memory_byte _parameters args =
    match args with
    | [ address ] ->
        let byte = Memory.read (Memory.integer address) in
        [ Native.NV_Literal
            (AST.L_BitVector (Bitvector.of_int_sized 8 byte)) ]
    | _ -> invalid_arg "ReadPhysicalMemoryByte takes one argument"

  let write_physical_memory_byte _parameters args =
    match args with
    | [ address; value ] ->
        Memory.write (Memory.integer address) (Memory.integer value);
        []
    | _ -> invalid_arg "WritePhysicalMemoryByte takes two arguments"

  let primitives =
    let read =
      primitive_decl ~returns:(named_ty "Byte")
        "ReadPhysicalMemoryByte" [ ("address", named_ty "Word") ]
    in
    let write =
      primitive_decl ~side_effecting:true "WritePhysicalMemoryByte"
        [ ("address", named_ty "Word"); ("value", named_ty "Byte") ]
    in
    [ (read, read_physical_memory_byte); (write, write_physical_memory_byte) ]
    @ Native.DeterministicBackend.primitives
end

module InterpreterConfig = struct
  module Instr = Instrumentation.SemanticsNoInstr

  let unroll = 0
  let recursive_unroll _ = None
  let error_handling_time = Error.Dynamic
  let empty_branching_effects_optimization = true
  let log_nondet_choice = false
  let display_call_stack_on_error = false
  let track_symbolic_path = false
  let bit_clear_optimisation = false
  let out_buffer = None
end

module HostInterpreter = Interpreter.Make (HostBackend) (InterpreterConfig)

let type_check_config =
  (module struct
    let check = Typing.Silence
    let output_format = Error.HumanReadable
    let print_typed = false
    let use_field_getter_extension = false
    let fine_grained_side_effects = false
    let use_conflicting_side_effects_extension = false
    let override_mode = Typing.Permissive
    let err_buffer = None
  end : Typing.ANNOTATE_CONFIG)

let exit_value = function
  | Native.NV_Literal (AST.L_Int value) -> Z.to_int value
  | _ -> invalid_arg "ASL main must return an integer"

let run model_path =
  let ast = Builder.from_file `ASLv1 model_path in
  let ast = Builder.with_stdlib ~no_stdlib0:true ast in
  let ast = Builder.with_primitives HostBackend.primitives ast in
  let module T = Typing.Annotate (val type_check_config) in
  let typed_ast, static_env = T.type_check_ast ast in
  let main_name = T.find_main static_env in
  HostInterpreter.run_typed static_env main_name typed_ast |> exit_value

let model_path () =
  match Array.to_list Sys.argv with
  | [ _; path ] -> path
  | [ _; "--no-type-check"; path ] -> path
  | _ ->
      Printf.eprintf "usage: %s [--no-type-check] <model.asl>\n%!" Sys.argv.(0);
      exit 2

let () =
  try exit (run (model_path ()))
  with exn ->
    Printf.eprintf "host-memory ASL runner failed: %s\n%!"
      (Printexc.to_string exn);
    exit 1
