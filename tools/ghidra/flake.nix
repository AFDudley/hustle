{
  description = "Container image for hustle's Ghidra headless decompiler. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        image = pkgs.callPackage ./nix/image.nix { };
        default = image;
      };
    };
}
