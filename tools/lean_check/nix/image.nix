{
  pkgs,
  dockerTools,
  aeneasTools,
  leanToolchain,
}:

# The lean_check translation-toolchain image. See ../README.md, "The
# container". Nothing here is compiled from OCaml or Lean source:
# aeneasTools and leanToolchain each unpack a prebuilt release and
# patch its ELF files for Nix's dynamic loader (see aeneas-tools.nix,
# lean-toolchain.nix).
#
# The Rust nightly toolchain is the one component this image does not
# bake in; see ../README.md, "The container", "Why the Rust toolchain
# installs at run time, not at image build time". `rustup`
# (nixpkgs' own package) is baked in instead, and
# `podman/in_container.sh` runs `rustup toolchain install` once per
# container start, caching the result under the mounted
# `.scratch/lean_check/cache/rustup`.
#
# `lake update` and `lake exe cache get`, run by podman/in_container.sh,
# also still reach the network at container run time (git clones,
# mathlib's prebuilt cache): git, curl and cacert below are for that
# network access at run time, not for building the image.
let
  rustToolchainName = "nightly-2026-09-17-x86_64-unknown-linux-gnu";
in
dockerTools.buildLayeredImage {
  name = "localhost/hustle-lean-check";
  tag = "latest";

  contents = [
    pkgs.bashInteractive
    pkgs.coreutils
    pkgs.gnugrep
    pkgs.gnused
    pkgs.gnutar
    pkgs.gzip
    pkgs.which
    pkgs.git
    pkgs.curl
    pkgs.cacert
    # cargo's default linker on Linux is `cc`; the Rust nightly
    # installs no C compiler of its own (the pre-Nix podman/Containerfile
    # pulled one in through Debian's build-essential).
    pkgs.gcc
    # charon's own CLI calls `rustup toolchain install
    # nightly-2026-09-17 ...` itself, every run (checked directly:
    # neither a directory override nor RUSTUP_TOOLCHAIN stops it, and a
    # Nix fixed-output derivation that pre-installs it hits a
    # certificate-verification failure specific to rustup's own HTTP
    # client inside the Nix build sandbox -- see the README section
    # named above). `rustup` itself bakes in fine; only the toolchain
    # it manages does not.
    pkgs.rustup
    # aeneasTools' own output places charon, charon-driver, aeneas and
    # the Lean `aeneas` package under opt/aeneas-tools, a literal path
    # ../generated/lakefile.lean names, so it must be a real directory
    # in the image.
    aeneasTools
  ];

  # dockerTools' base image has no /tmp (cargo's rustdoc uses it for
  # doctests) and no /root (HOME, below; also RUSTUP_HOME and
  # CARGO_HOME by default). /root is a real, writable directory, not a
  # Nix store symlink: `rustup toolchain install` writes into it.
  #
  # /lib64/ld-linux-x86-64.so.2: `lake exe cache get` (run by
  # podman/in_container.sh) links a real standalone executable,
  # mathlib's own `cache` tool, through leanToolchain's bundled clang.
  # That link step hardcodes the standard FHS interpreter path, not a
  # Nix store path (`readelf -p .interp` on the built binary showed
  # exactly this path); every other real Linux distribution has it,
  # but a Nix-built image does not, so bash's "cannot execute:
  # required file not found" is this missing path, not a missing
  # shared library. pkgs.glibc's own copy of it, symlinked here,
  # fixes any such binary this pipeline links, not just `cache`.
  extraCommands = ''
    mkdir -p tmp
    chmod 1777 tmp
    mkdir -p root
    chmod 0700 root
    mkdir -p lib64
    ln -s ${pkgs.glibc}/lib/ld-linux-x86-64.so.2 lib64/ld-linux-x86-64.so.2
  '';

  config = {
    Env = [
      "PATH=/opt/aeneas-tools:${leanToolchain}/bin:/bin"
      "HOME=/root"
      # Charon-driver (in aeneasTools) needs librustc_driver*.so and
      # libLLVM*.so from the Rust nightly `rustup` installs into
      # /root/.rustup at container run time (see "contents" above).
      # This path does not exist at image build time; it is a plain
      # string here, resolved only when the container actually runs.
      "LD_LIBRARY_PATH=/root/.rustup/toolchains/${rustToolchainName}/lib"
      "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
      "GIT_SSL_CAINFO=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
      "LEAN_CHECK_RUST_TOOLCHAIN_NAME=${rustToolchainName}"
      "LEAN_CHECK_CONTAINER=1"
    ];
  };
}
