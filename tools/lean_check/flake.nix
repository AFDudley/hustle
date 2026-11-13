{
  description = "Container image for hustle's Rust-to-Lean translation check. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        aeneasTools = pkgs.callPackage ./nix/aeneas-tools.nix { };
        leanToolchain = pkgs.callPackage ./nix/lean-toolchain.nix { };
        image = pkgs.callPackage ./nix/image.nix {
          inherit aeneasTools leanToolchain;
        };
        default = image;
      };
    };
}
