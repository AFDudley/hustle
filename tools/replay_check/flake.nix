{
  description = "Container image for hustle's headless replay check. See README.md.";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system} = rec {
        godotsteam = pkgs.callPackage ./nix/godotsteam.nix { };
        audioSleepShim = pkgs.callPackage ./nix/audio-sleep-shim.nix {
          src = ./audio_sleep_shim.c;
        };
        netsim = pkgs.callPackage ./nix/netsim.nix { src = ./netsim; };
        image = pkgs.callPackage ./nix/image.nix {
          inherit godotsteam audioSleepShim netsim;
        };
        default = image;
      };
    };
}
