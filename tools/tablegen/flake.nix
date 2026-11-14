{
  description = "Container image for hustle's table generator (Python plus fixed_parse). See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        fixedParse = pkgs.callPackage ./nix/rust.nix { src = ./.; };
        image = pkgs.callPackage ./nix/image.nix { inherit fixedParse; };
        default = image;
      };
    };
}
