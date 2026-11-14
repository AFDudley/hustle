{
  description = "Container image for hustle's RNG/hash diff Rust stage and comparison. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        rngDiff = pkgs.callPackage ./nix/rust.nix { src = ./.; };
        image = pkgs.callPackage ./nix/image.nix { inherit rngDiff; };
        default = image;
      };
    };
}
