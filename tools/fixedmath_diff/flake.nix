{
  description = "Container image for hustle's FixedMath diff Rust stage and comparison. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        fixedmathDiff = pkgs.callPackage ./nix/rust.nix { src = ./rust; };
        image = pkgs.callPackage ./nix/image.nix { inherit fixedmathDiff; };
        default = image;
      };
    };
}
