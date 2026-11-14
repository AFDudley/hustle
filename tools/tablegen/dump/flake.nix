{
  description = "Container image for hustle's table_dump binary. See ../README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        tableDump = pkgs.callPackage ./nix/rust.nix { src = ./.; };
        image = pkgs.callPackage ./nix/image.nix { inherit tableDump; };
        default = image;
      };
    };
}
