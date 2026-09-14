(*
 * A small host protocol around the pinned ASLRef interpreter.
 *
 * This file deliberately lives outside herdtools7.  It links the public
 * Asllib library produced by the pinned checkout and keeps the interpreter,
 * typed AST, and architectural state alive for the lifetime of the process.
 *)

open Asllib

module Protocol = struct
  type pending = {
    instruction : Z.t;
    length_bits : int;
    index : int;
    pe : int;
    address : Z.t;
    value : int;
  }

  let pending : pending option ref = ref None

  let split_words line =
    line
    |> String.split_on_char ' '
    |> List.filter (fun word -> String.length word > 0)

  let parse_step words =
    match words with
    | [ "step"; instruction; length_bits ] ->
        let instruction =
          try Z.of_string instruction
          with _ -> invalid_arg "step instruction must be an integer literal"
        in
        let length_bits =
          try int_of_string length_bits
          with _ -> invalid_arg "step length must be an integer"
        in
        pending := Some {
          instruction; length_bits; index = 0; pe = 0; address = Z.zero; value = 0
        };
        1
    | [ "reset" ] ->
        pending := None;
        2
    | [ "ping" ] -> 3
    | [ "peek_gpr"; index ] ->
        let index =
          try int_of_string index
          with _ -> invalid_arg "peek_gpr index must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index; pe = 0;
          address = Z.zero; value = 0
        };
        4
    | [ "peek_tpc" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        5
    | [ "decode_length"; instruction ] ->
        let instruction =
          try Z.of_string instruction
          with _ -> invalid_arg "decode_length instruction must be an integer literal"
        in
        pending := Some {
          instruction; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        6
    | [ "peek_fault" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        7
    | [ "read_mem"; address ] ->
        let address =
          try Z.of_string address
          with _ -> invalid_arg "read_mem address must be an integer literal"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address; value = 0
        };
        8
    | [ "write_mem"; address; value ] ->
        let address =
          try Z.of_string address
          with _ -> invalid_arg "write_mem address must be an integer literal"
        in
        let value =
          try int_of_string value
          with _ -> invalid_arg "write_mem value must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address; value
        };
        9
    | [ "select_pe"; pe ] ->
        let pe =
          try int_of_string pe
          with _ -> invalid_arg "select_pe PE id must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe;
          address = Z.zero; value = 0
        };
        10
    | [ "peek_pe_gpr"; pe; index ] ->
        let pe =
          try int_of_string pe
          with _ -> invalid_arg "peek_pe_gpr PE id must be an integer"
        in
        let index =
          try int_of_string index
          with _ -> invalid_arg "peek_pe_gpr index must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index; pe;
          address = Z.zero; value = 0
        };
        11
    | [ "set_tpc"; value ] ->
        let value =
          try Z.of_string value
          with _ -> invalid_arg "set_tpc value must be an integer literal"
        in
        pending := Some {
          instruction = value; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        12
    | [ "peek_pe" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        13
    | [ "peek_terminal_pending" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        14
    | [ "peek_bundle_active" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        15
    | [ "peek_bundle_body_active" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        16
    | [ "peek_acr" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        17
    | [ "peek_control_request" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        18
    | [ "peek_barg_bpcn" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        20
    | [ "peek_barg_word" ] ->
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.zero; value = 0
        };
        21
    | [ "peek_shared"; shared_id ] ->
        let shared_id =
          try int_of_string shared_id
          with _ -> invalid_arg "peek_shared id must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe = 0;
          address = Z.of_int shared_id; value = 0
        };
        22
    | [ "step_auto" ] -> 23
    | [ "clear_mem_cache" ] -> 24
    | [ "install_pe_context"; pe ] ->
        let pe =
          try int_of_string pe
          with _ -> invalid_arg "install_pe_context PE id must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe;
          address = Z.zero; value = 0
        };
        25
    | [ "capture_pe_context"; pe ] ->
        let pe =
          try int_of_string pe
          with _ -> invalid_arg "capture_pe_context PE id must be an integer"
        in
        pending := Some {
          instruction = Z.zero; length_bits = 0; index = 0; pe;
          address = Z.zero; value = 0
        };
        26
    | [ "quit" ] -> 0
    | _ -> 255

  let read_command () =
    match input_line stdin with
    | line -> (
        try parse_step (split_words line)
        with Invalid_argument message ->
          Printf.eprintf "protocol error: %s\n%!" message;
          255)
    | exception End_of_file -> 0

  let require_pending () =
    match !pending with
    | Some request -> request
    | None -> invalid_arg "instruction requested without a step command"

  let read_instruction () =
    let request = require_pending () in
    Native.NV_Literal
      (AST.L_BitVector (Bitvector.of_z 64 request.instruction))

  let read_length () =
    let request = require_pending () in
    Native.NV_Literal (AST.L_Int (Z.of_int request.length_bits))

  let read_index () =
    let request = require_pending () in
    Native.NV_Literal (AST.L_Int (Z.of_int request.index))

  let read_pe () =
    let request = require_pending () in
    Native.NV_Literal (AST.L_Int (Z.of_int request.pe))

  let read_address () =
    let request = require_pending () in
    Native.NV_Literal (AST.L_BitVector (Bitvector.of_z 64 request.address))

  let read_byte_value () =
    let request = require_pending () in
    Native.NV_Literal (AST.L_Int (Z.of_int request.value))

  let write_status value =
    let status =
      match value with
      | Native.NV_Literal (AST.L_Int value) -> Z.to_string value
      | _ -> "invalid"
    in
    Printf.printf "status %s\n%!" status;
    []

  let write_value value =
    let value =
      match value with
      | Native.NV_Literal (AST.L_Int value) -> Z.to_string value
      | _ -> "invalid"
    in
    Printf.printf "value %s\n%!" value;
    []

  let write_memory_status () =
    Printf.printf "status 0\n%!";
    []

  let memory_request command =
    Printf.printf "%s\n%!" command;
    match input_line stdin with
    | line -> line
    | exception End_of_file -> invalid_arg "memory host closed the protocol"

  let memory_address_value = function
    | [ Native.NV_Literal (AST.L_BitVector address) ] ->
        Bitvector.to_z_unsigned address
    | [ Native.NV_Literal (AST.L_Int address) ] -> address
    | _ -> invalid_arg "memory address must be a bitvector"

  let memory_address_argument args = Z.to_string (memory_address_value args)

  let memory_value_argument = function
    | [ Native.NV_Literal (AST.L_BitVector value) ] ->
        Z.to_int (Bitvector.to_z_unsigned value)
    | _ -> invalid_arg "memory value must be a bitvector"

  let memory_value_response line =
    match String.split_on_char ' ' line with
    | [ "mem_value"; value ] ->
        let value = try int_of_string value with _ -> invalid_arg "invalid memory value" in
        if value < 0 || value > 255 then invalid_arg "memory value is not a byte";
        Native.NV_Literal (AST.L_BitVector (Bitvector.of_int_sized 8 value))
    | _ -> invalid_arg "invalid memory read response"

  let memory_status_response line =
    if line <> "mem_status 0" then invalid_arg "invalid memory write response"
end

module MemoryCache = struct
  let bytes : (Z.t, int) Hashtbl.t = Hashtbl.create 65536
  let max_cached_bytes = 262144

  let clear () = Hashtbl.clear bytes

  let hex_digit = function
    | '0' .. '9' as value -> Char.code value - Char.code '0'
    | 'a' .. 'f' as value -> Char.code value - Char.code 'a' + 10
    | 'A' .. 'F' as value -> Char.code value - Char.code 'A' + 10
    | _ -> invalid_arg "invalid memory chunk hex digit"

  let load_response line =
    match Protocol.split_words line with
    | [ "mem_chunk"; base; payload ] ->
        let base =
          try Z.of_string base
          with _ -> invalid_arg "invalid memory chunk base"
        in
        let length = String.length payload in
        if length = 0 || length mod 2 <> 0 then
          invalid_arg "invalid memory chunk payload";
        let incoming_bytes = length / 2 in
        if incoming_bytes > max_cached_bytes then
          invalid_arg "memory chunk exceeds cache capacity";
        if Hashtbl.length bytes + incoming_bytes > max_cached_bytes then
          clear ();
        for index = 0 to incoming_bytes - 1 do
          let high = hex_digit payload.[index * 2] in
          let low = hex_digit payload.[index * 2 + 1] in
          Hashtbl.replace bytes (Z.add base (Z.of_int index)) (high * 16 + low)
        done
    | _ -> invalid_arg "invalid memory chunk response"

  let read address =
    match Hashtbl.find_opt bytes address with
    | Some value -> value
    | None ->
        load_response
          (Protocol.memory_request
             (Printf.sprintf "mem_read_chunk %s 4096" (Z.to_string address)));
        (match Hashtbl.find_opt bytes address with
        | Some value -> value
        | None -> invalid_arg "memory chunk omitted requested address")

  let write address value =
    if not (Hashtbl.mem bytes address) &&
       Hashtbl.length bytes >= max_cached_bytes then
      clear ();
    Hashtbl.replace bytes address value
end

let primitive_decl ?returns ?(side_effecting = false) name args =
  let open AST in
  {
    name;
    parameters = [];
    args;
    body = SB_Primitive side_effecting;
    return_type = returns;
    subprogram_type = (match returns with None -> ST_Procedure | Some _ -> ST_Function);
    recurse_limit = None;
    qualifier = if side_effecting then None else Some Pure;
    override = None;
    builtin = true;
  }

let bits_ty width =
  AST.T_Bits (ASTUtils.expr_of_int width, []) |> ASTUtils.add_dummy_pos

module HostBackend = struct
  include Native.DeterministicBackend

  let host_read_command _parameters args =
    if args <> [] then invalid_arg "HostReadCommand takes no arguments";
    [ Native.NV_Literal (AST.L_Int (Z.of_int (Protocol.read_command ()))) ]

  let host_read_instruction _parameters args =
    if args <> [] then invalid_arg "HostReadInstruction takes no arguments";
    [ Protocol.read_instruction () ]

  let host_read_length _parameters args =
    if args <> [] then invalid_arg "HostReadLength takes no arguments";
    [ Protocol.read_length () ]

  let host_read_index _parameters args =
    if args <> [] then invalid_arg "HostReadIndex takes no arguments";
    [ Protocol.read_index () ]

  let host_read_pe _parameters args =
    if args <> [] then invalid_arg "HostReadPE takes no arguments";
    [ Protocol.read_pe () ]

  let host_write_status _parameters args =
    match args with
    | [ value ] -> Protocol.write_status value
    | _ -> invalid_arg "HostWriteStatus takes one argument"

  let host_write_value _parameters args =
    match args with
    | [ value ] -> Protocol.write_value value
    | _ -> invalid_arg "HostWriteValue takes one argument"

  let host_write_step_result _parameters args =
    let as_int = function
      | Native.NV_Literal (AST.L_Int value) -> Z.to_string value
      | _ -> invalid_arg "step result fields must be integers"
    in
    match args with
    | [ status; length; fault; tpc; instruction ] ->
        let as_integer = function
          | Native.NV_Literal (AST.L_BitVector value) ->
              Z.to_string (Bitvector.to_z_unsigned value)
          | value -> as_int value
        in
        Printf.printf "step_result %s %s %s %s %s\n%!"
          (as_int status) (as_int length) (as_int fault) (as_int tpc)
          (as_integer instruction);
        []
    | _ -> invalid_arg "HostWriteStepResult takes five arguments"

  let host_read_memory_byte _parameters args =
    let value = MemoryCache.read (Protocol.memory_address_value args) in
    [ Native.NV_Literal
        (AST.L_BitVector (Bitvector.of_int_sized 8 value)) ]

  let host_read_memory_address _parameters args =
    if args <> [] then invalid_arg "HostReadMemoryAddress takes no arguments";
    [ Protocol.read_address () ]

  let host_read_memory_value _parameters args =
    if args <> [] then invalid_arg "HostReadMemoryValue takes no arguments";
    [ Protocol.read_byte_value () ]

  let host_write_memory_byte _parameters args =
    let as_z = function
      | Native.NV_Literal (AST.L_BitVector value) -> Bitvector.to_z_unsigned value
      | Native.NV_Literal (AST.L_Int value) -> value
      | _ -> invalid_arg "memory argument must be an integer or bitvector"
    in
    match args with
    | [ address; value ] ->
        let address = as_z address in
        let value = Z.to_int (as_z value) in
        if value < 0 || value > 255 then invalid_arg "memory value is not a byte";
        Protocol.memory_status_response
          (Protocol.memory_request
             (Printf.sprintf "mem_write %s %d" (Z.to_string address) value));
        MemoryCache.write address value;
        []
    | _ -> invalid_arg "HostWriteMemoryByte takes two bitvector arguments"

  let host_clear_memory_cache _parameters args =
    if args <> [] then invalid_arg "HostClearMemoryCache takes no arguments";
    MemoryCache.clear ();
    []

  (* Hosted-profile access permission hooks.  They are only reached when the
     generated ASL enables PTO_MODEL_HOST_MEMORY, in which case the host memory
     bridge owns mapping and serves unmapped holes with deterministic
     zero-backed sparse pages.  The byte-level reads and writes still travel
     through HostReadMemoryByte/HostWriteMemoryByte, so real host failures are
     still reported; the preflight check therefore grants the access here. *)
  let host_instruction_access_permitted _parameters _args =
    [ Native.NV_Literal (AST.L_Bool true) ]

  let host_data_access_permitted _parameters _args =
    [ Native.NV_Literal (AST.L_Bool true) ]

  let primitives =
    let open ASTUtils in
    let command =
      primitive_decl ~side_effecting:true ~returns:integer "HostReadCommand" []
    in
    let instruction =
      primitive_decl ~side_effecting:true ~returns:(bits_ty 64)
        "HostReadInstruction" []
    in
    let length =
      primitive_decl ~side_effecting:true ~returns:integer "HostReadLength" []
    in
    let index =
      primitive_decl ~side_effecting:true ~returns:integer "HostReadIndex" []
    in
    let pe =
      primitive_decl ~side_effecting:true ~returns:integer "HostReadPE" []
    in
    let status =
      primitive_decl ~side_effecting:true "HostWriteStatus" [ ("value", integer) ]
    in
    let value =
      primitive_decl ~side_effecting:true "HostWriteValue" [ ("value", integer) ]
    in
    let step_result =
      primitive_decl ~side_effecting:true "HostWriteStepResult"
        [ ("status", integer); ("length", integer); ("fault", integer);
          ("tpc", integer); ("instruction", bits_ty 64) ]
    in
    let memory_read =
      primitive_decl ~returns:(bits_ty 8)
        "HostReadMemoryByte" [ ("address", bits_ty 64) ]
    in
    let memory_write =
      primitive_decl ~side_effecting:true
        "HostWriteMemoryByte" [ ("address", bits_ty 64); ("value", bits_ty 8) ]
    in
    let memory_address =
      primitive_decl ~side_effecting:true ~returns:(bits_ty 64)
        "HostReadMemoryAddress" []
    in
    let memory_value =
      primitive_decl ~side_effecting:true ~returns:(bits_ty 8)
        "HostReadMemoryValue" []
    in
    let instruction_access =
      primitive_decl ~returns:boolean
        "HostInstructionAccessPermitted"
        [ ("address", bits_ty 64); ("size_bytes", integer) ]
    in
    let data_access =
      primitive_decl ~returns:boolean
        "HostDataAccessPermitted"
        [ ("address", bits_ty 64); ("size_bytes", integer);
          ("write", boolean) ]
    in
    let clear_memory_cache =
      primitive_decl ~side_effecting:true "HostClearMemoryCache" []
    in
    [ (command, host_read_command);
      (instruction, host_read_instruction);
      (length, host_read_length);
      (index, host_read_index);
      (pe, host_read_pe);
      (status, host_write_status) ]
    @ [ (value, host_write_value); (step_result, host_write_step_result);
        (memory_read, host_read_memory_byte);
        (memory_write, host_write_memory_byte); (memory_address, host_read_memory_address);
        (memory_value, host_read_memory_value) ]
    @ [ (instruction_access, host_instruction_access_permitted);
        (data_access, host_data_access_permitted);
        (clear_memory_cache, host_clear_memory_cache) ]
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

let wrapper_source initial_source =
  let initial_source =
    if String.trim initial_source = "" then "    pass;" else initial_source
  in
  Printf.sprintf
    {|
func AslModelInitialState()
begin
%s
end;

func FaultValue() => integer
begin
    case _LastFault of
        when Fault_None => return 0;
        when Fault_ExecutionStateCheck => return 1;
        when Fault_IllegalInstruction => return 2;
        when Fault_InstructionPC => return 3;
        when Fault_InstructionPage => return 4;
        when Fault_DataAlignment => return 5;
        when Fault_DataPage => return 6;
        when Fault_SoftwareBreakpoint => return 7;
        when Fault_HardwareBreakpoint => return 8;
        when Fault_HardwareWatchpoint => return 9;
        when Fault_Assert => return 10;
        when Fault_TileLegality => return 11;
        when Fault_TileAllocation => return 12;
        when Fault_BundleControl => return 13;
        when Fault_BundlePostCommit => return 14;
        when Fault_ServiceRequest => return 15;
    end;
end;

func ASLOwnedInstructionLength(instruction: bits(64)) => integer
begin
    // PTO-SPEC owns the total low-halfword length rule, including encodings
    // that will subsequently be rejected as illegal.
    return DeterminePTOInstructionLength(instruction[15:0]);
end;

func main() => integer
begin
    ResetProfileState();
    AslModelInitialState();
    var running: boolean = TRUE;
    while running looplimit 1000000 do
        let command = HostReadCommand();
        if command == 0 then
            running = FALSE;
        elsif command == 1 then
            let instruction = HostReadInstruction();
            let length = HostReadLength();
            if length == 16 then
                let status = ExecutePTOInstruction(instruction, 16);
                if status == PTOInstruction_Executed then HostWriteStatus(0);
                else HostWriteStatus(1); end;
            elsif length == 32 then
                let status = ExecutePTOInstruction(instruction, 32);
                if status == PTOInstruction_Executed then HostWriteStatus(0);
                else HostWriteStatus(1); end;
            elsif length == 48 then
                let status = ExecutePTOInstruction(instruction, 48);
                if status == PTOInstruction_Executed then HostWriteStatus(0);
                else HostWriteStatus(1); end;
            elsif length == 64 then
                let status = ExecutePTOInstruction(instruction, 64);
                if status == PTOInstruction_Executed then HostWriteStatus(0);
                else HostWriteStatus(1); end;
            else
                HostWriteStatus(1);
            end;
        elsif command == 2 then
            ResetProfileState();
            AslModelInitialState();
            HostWriteStatus(0);
        elsif command == 3 then
            HostWriteStatus(0);
        elsif command == 4 then
            let index = HostReadIndex();
            HostWriteValue(UInt(ReadGPR(index as GPRIndex)));
        elsif command == 5 then
            HostWriteValue(UInt(ReadTPC()));
        elsif command == 6 then
            let instruction = HostReadInstruction();
            HostWriteValue(ASLOwnedInstructionLength(instruction));
        elsif command == 7 then
            HostWriteValue(FaultValue());
        elsif command == 8 then
            let address = HostReadMemoryAddress();
            HostWriteValue(UInt(HostReadMemoryByte(address)));
        elsif command == 9 then
            let address = HostReadMemoryAddress();
            let value = HostReadMemoryValue();
            HostWriteMemoryByte(address, value);
            HostWriteStatus(0);
        elsif command == 10 then
            let pe = HostReadPE();
            SelectMemoryEventAgent(pe as MemoryAgentId);
            HostWriteStatus(0);
        elsif command == 11 then
            let pe = HostReadPE();
            let index = HostReadIndex();
            HostWriteValue(UInt(ReadPEGPR(pe as MemoryAgentId,
                index as GPRIndex)));
        elsif command == 12 then
            WriteTPC(HostReadInstruction());
            HostWriteStatus(0);
        elsif command == 13 then
            HostWriteValue(_CurrentMemoryAgent);
        elsif command == 14 then
            HostWriteValue(if _SystemBlockTerminalPending then 1 else 0);
        elsif command == 15 then
            HostWriteValue(if _BundleActive then 1 else 0);
        elsif command == 16 then
            HostWriteValue(if _BundleBodyActive then 1 else 0);
        elsif command == 17 then
            HostWriteValue(_CurrentACR);
        elsif command == 18 then
            HostWriteValue(UInt(_ControlRequestOperand));
        elsif command == 20 then
            HostWriteValue(UInt(_BARG.bpcn));
        elsif command == 21 then
            HostWriteValue(UInt(PackCurrentBARGControlWord()));
        elsif command == 22 then
            let shared = SharedTileRecord(HostReadMemoryAddress()[5:0] as SharedTileID);
            var flags: bits(4) = Zeros{4};
            if shared.descriptor_valid then flags = flags OR '0001'; end;
            if shared.whole_parent_ready then flags = flags OR '0010'; end;
            if shared.published then flags = flags OR '0100'; end;
            if shared.tile.contents_defined then flags = flags OR '1000'; end;
            HostWriteValue(UInt(flags));
        elsif command == 23 then
            // ExecuteNextPTOInstruction is the generated PTO ASL contract:
            // ASL checks PC/alignment, fetches only the selected width,
            // decodes, executes, and returns the transition status.  The
            // wrapper pre-reads the instruction only to report its width and
            // encoding; execution and faults stay in the ASL boundary.
            let step_pc = ReadTPC();
            var length_bits: integer {16,32,48,64} = 16;
            var instruction: bits(64) = Zeros{64};
            let prefix_probe = ProbeInstructionAccess(step_pc, 2);
            if prefix_probe.permitted then
                let prefix = FetchPTOInstruction(prefix_probe, 16);
                length_bits = DeterminePTOInstructionLength(prefix[15:0]);
                let size_bytes = (length_bits DIV 8) as integer {2,4,6,8};
                let complete_probe = ProbeInstructionAccess(step_pc, size_bytes);
                if complete_probe.permitted then
                    instruction = FetchPTOInstruction(complete_probe, length_bits);
                end;
            end;
            let result = ExecuteNextPTOInstruction();
            var status: integer = 1;
            if result == PTOInstruction_Executed then status = 0; end;
            HostWriteStepResult(status, length_bits, FaultValue(),
                UInt(ReadTPC()), instruction);
        elsif command == 24 then
            HostClearMemoryCache();
            HostWriteStatus(0);
        elsif command == 25 then
            // PTO-ARCH-PROGRAMMING-MODEL-PE-CONTEXT: make one PE's stored
            // execution context the live one.  The host calls this before
            // stepping a PE so its program counter, bundle lifecycle, commit
            // argument, predicate file, temporary queues and fault state are
            // the PE's own rather than the previous PE's leftovers.
            let pe = HostReadPE();
            InstallPEContext(pe as MemoryAgentId);
            HostWriteStatus(0);
        elsif command == 26 then
            let pe = HostReadPE();
            CapturePEContext(pe as MemoryAgentId);
            HostWriteStatus(0);
        else
            HostWriteStatus(2);
        end;
    end;
    return 0;
end;
|}
    initial_source

let type_check_config =
  (module struct
    let check = Typing.TypeCheck
    let output_format = Error.HumanReadable
    let print_typed = false
    let use_field_getter_extension = false
    let fine_grained_side_effects = false
    let use_conflicting_side_effects_extension = false
    let override_mode = Typing.Permissive
    let err_buffer = None
  end : Typing.ANNOTATE_CONFIG)

let read_optional_file path =
  if path = "" then ""
  else
    let channel = open_in path in
    Fun.protect
      ~finally:(fun () -> close_in_noerr channel)
      (fun () -> really_input_string channel (in_channel_length channel))

let run spec_path initial_source_path =
  let spec = Builder.from_file `ASLv1 spec_path in
  let initial_source = read_optional_file initial_source_path in
  let wrapper =
    Builder.from_string ~filename:"asl-model-worker-wrapper.asl"
      ~ast_string:(wrapper_source initial_source) `ASLv1
  in
  let ast = spec @ wrapper in
  let ast = Builder.with_stdlib ~no_stdlib0:true ast in
  let ast = Builder.with_primitives HostBackend.primitives ast in
  let module T = Typing.Annotate (val type_check_config) in
  let typed_ast, static_env = T.type_check_ast ast in
  let main_name = T.find_main static_env in
  ignore (HostInterpreter.run_typed static_env main_name typed_ast)

let () =
  if Array.length Sys.argv < 2 || Array.length Sys.argv > 3 then begin
    Printf.eprintf "usage: %s <pto-spec.asl> [initial-source.asl]\n%!" Sys.argv.(0);
    exit 2
  end;
  let initial_source_path = if Array.length Sys.argv = 3 then Sys.argv.(2) else "" in
  try run Sys.argv.(1) initial_source_path
  with exn ->
    Printf.eprintf "ASL worker failed: %s\n%!" (Printexc.to_string exn);
    exit 1
