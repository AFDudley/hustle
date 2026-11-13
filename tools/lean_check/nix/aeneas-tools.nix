{
  stdenv,
  fetchurl,
  autoPatchelfHook,
}:

# Charon and Aeneas, prebuilt release binaries (no OCaml build on this
# host). See ../README.md, "Tool versions".
#
# The pre-Nix podman/Containerfile extracted charon.tar.gz first, then
# aeneas.tar.gz on top of it, into one directory (/opt/aeneas-tools).
# aeneas's own release tarball bundles its own copies of charon,
# charon-driver and rust-toolchain (seen with `tar tzf`), so this
# derivation keeps that same order: aeneas's copies win where the two
# tarballs overlap, matching the pipeline run ../README.md records.
#
# charon-driver runs as a custom rustc, so it needs librustc_driver*.so
# and libLLVM*.so, not from here, but from the Rust nightly toolchain
# `podman/in_container.sh` installs with `rustup` at container run
# time (see ../README.md, "The container", "Why the Rust toolchain
# installs at run time, not at image build time"). ../nix/image.nix
# sets LD_LIBRARY_PATH to that toolchain's lib/ directory so
# charon-driver finds it there. autoPatchelfHook here only resolves
# glibc and libgcc_s. aeneas itself is a statically linked OCaml binary
# (readelf -h: EXEC, no dynamic section): nothing to patch there.
#
# The result is installed under $out/opt/aeneas-tools, not $out itself:
# ../generated/lakefile.lean names the literal, absolute path
# /opt/aeneas-tools/backends/lean (`require aeneas from
# "/opt/aeneas-tools/backends/lean"`), and that file is not part of this
# migration, so the path it names must still exist.
stdenv.mkDerivation {
  pname = "aeneas-tools";
  version = "nightly-2026.11.13";

  charonSrc = fetchurl {
    url = "https://github.com/AeneasVerif/charon/releases/download/nightly-2026.11.13/charon-linux-x86_64.tar.gz";
    sha256 = "f0e7f56425c76f391dadcf246ccd0a7a7e24d9b43de10828464fd6dc5a681b3a";
  };
  aeneasSrc = fetchurl {
    url = "https://github.com/AeneasVerif/aeneas/releases/download/nightly-2026.11.13-0d21a9e/aeneas-linux-x86_64.tar.gz";
    sha256 = "39c2565790310acdfb8fe4d12a1a4d637e85c7a1672e7d4586cb7c4d9bfe446e";
  };

  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];

  # librustc_driver*.so and libLLVM*.so are not present anywhere at Nix
  # build time (see the comment above): the Rust toolchain that
  # provides them does not exist until container run time. Without
  # this, autoPatchelfHook fails the build outright ("could not
  # satisfy dependency"), the same way it did for libz.so.1 before
  # zlib was added as a buildInput for the Rust toolchain attempt this
  # derivation no longer makes.
  autoPatchelfIgnoreMissingDeps = [
    "librustc_driver-*.so"
    "libLLVM.so.*"
  ];

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/opt/aeneas-tools"
    tar -xzf "$charonSrc" -C "$out/opt/aeneas-tools"
    tar -xzf "$aeneasSrc" -C "$out/opt/aeneas-tools"
    chmod +x "$out"/opt/aeneas-tools/charon "$out"/opt/aeneas-tools/charon-driver \
      "$out"/opt/aeneas-tools/aeneas
    runHook postInstall
  '';

  meta.platforms = [ "x86_64-linux" ];
}
