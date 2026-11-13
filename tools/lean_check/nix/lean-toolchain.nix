{
  stdenv,
  fetchurl,
  unzip,
  autoPatchelfHook,
}:

# The Lean toolchain the generated project pins, v4.36.0-rc2 (see
# ../generated/lean-toolchain), fetched directly instead of through elan:
# this image serves exactly one project, so there is only ever one
# toolchain to select, and elan's job (reading a project's own
# lean-toolchain file and fetching that version) is already done once,
# here, by hand.
#
# lean, lake and the bundled clang all carry a RUNPATH of $ORIGIN/../lib
# (readelf -d), and clang's own shared-library dependencies (libLLVM,
# libclang-cpp, libc++, libc++abi, even its own libgcc_s.so.1) ship inside
# lib/ already. Only the ELF interpreter and glibc (libc, libpthread,
# libdl, libm) are external; autoPatchelfHook sets those, the same
# technique as ../../replay_check/nix/godotsteam.nix.
stdenv.mkDerivation {
  pname = "lean-toolchain";
  version = "4.36.0-rc2";

  src = fetchurl {
    url = "https://github.com/leanprover/lean4/releases/download/v4.36.0-rc2/lean-4.36.0-rc2-linux.zip";
    sha256 = "1q937amff6ka71i8dx5q92bkdagyxi5ia4ddymb0ygxn07r28n3a";
  };

  nativeBuildInputs = [
    unzip
    autoPatchelfHook
  ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -a . "$out"/
    runHook postInstall
  '';

  # Covers any binary in this release that does not already carry its
  # own $ORIGIN-relative RUNPATH to lib/.
  preFixup = ''
    addAutoPatchelfSearchPath "$out/lib"
  '';

  meta.platforms = [ "x86_64-linux" ];
}
