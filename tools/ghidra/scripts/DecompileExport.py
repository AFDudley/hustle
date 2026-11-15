# Ghidra headless post-script. See ../README.md and ../container.sh.
#
# Script arguments (space-separated, passed after -postScript in
# container.sh): an output file path, then one or more function
# names or hex addresses (e.g. "0x63b00").
#
# Writes, for each requested function: its demangled name, its entry
# address, and the decompiler's C output, to the output file. A
# function not found (by name or by address) gets one line saying so,
# instead of silently skipping it.

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
if len(args) < 2:
    print("usage: DecompileExport.py <out_file> <name_or_addr> [more...]")
    exit(1)

out_path = args[0]
targets = args[1:]

decomp = DecompInterface()
decomp.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()

fm = currentProgram.getFunctionManager()

def find_function(target):
    # Try as a hex address first (e.g. "0x63b00" or "63b00").
    addr_text = target[2:] if target.lower().startswith("0x") else target
    try:
        addr = currentProgram.getAddressFactory().getAddress("0x" + addr_text)
        f = fm.getFunctionAt(addr)
        if f is not None:
            return f
        f = fm.getFunctionContaining(addr)
        if f is not None:
            return f
    except Exception:
        pass
    # Fall back to a name match (exact, then substring) across every
    # function Ghidra found, since the binary's own symbols are Rust's
    # mangled names.
    it = fm.getFunctions(True)
    exact = None
    substr_matches = []
    while it.hasNext():
        f = it.next()
        if f.getName() == target:
            exact = f
            break
        if target in f.getName():
            substr_matches.append(f)
    if exact is not None:
        return exact
    if len(substr_matches) == 1:
        return substr_matches[0]
    if len(substr_matches) > 1:
        return substr_matches
    return None

with open(out_path, "w") as out:
    for target in targets:
        result = find_function(target)
        if result is None:
            out.write("=== %s: NOT FOUND ===\n\n" % target)
            continue
        if isinstance(result, list):
            out.write("=== %s: %d ambiguous matches ===\n" % (target, len(result)))
            for f in result:
                out.write("  %s at %s\n" % (f.getName(), f.getEntryPoint()))
            out.write("\n")
            continue
        f = result
        out.write("=== %s ===\n" % target)
        out.write("name: %s\n" % f.getName())
        out.write("entry: %s\n" % f.getEntryPoint())
        res = decomp.decompileFunction(f, 120, monitor)
        if res.decompileCompleted():
            out.write(res.getDecompiledFunction().getC())
        else:
            out.write("DECOMPILE FAILED: %s\n" % res.getErrorMessage())
        out.write("\n")

print("wrote " + out_path)
