{
  description = "Container image for hustle's FGObject diff Rust stage. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        fgobjectDiff = pkgs.callPackage ./nix/rust.nix { src = ./.; };
        image = pkgs.callPackage ./nix/image.nix { inherit fgobjectDiff; };
        default = image;
      };
    };
}
