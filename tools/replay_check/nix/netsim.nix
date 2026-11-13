{ rustPlatform, src }:

# udp-netsim, the latency and loss relay for network tests. See
# ../netsim/src/main.rs and README.md, "Network tests". The crate has no
# dependencies, so Cargo.lock needs no vendored sources.
rustPlatform.buildRustPackage {
  pname = "udp-netsim";
  version = "0.1.0";
  inherit src;
  cargoLock.lockFile = src + "/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
