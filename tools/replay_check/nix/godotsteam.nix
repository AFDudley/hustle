{
  stdenv,
  fetchurl,
  autoPatchelfHook,
  unzip,
  xorg,
  libglvnd,
}:

# GodotSteam g351-s155-gs3184, the Godot 3.5.1 editor build with the
# Steamworks SDK 1.55 module. See README.md, "The container".
#
# The zip ships five prebuilt glibc ELF files and one shell script. This
# package only unpacks them and patches their interpreter and RPATH for
# Nix's dynamic loader (autoPatchelfHook). It builds nothing from source.
#
# linux-351-editor.64 has RUNPATH "." for libsteam_api.so, so it finds that
# library only when the working directory is this one, or through
# LD_LIBRARY_PATH (see nix/image.nix). autoPatchelfHook also sees
# libsteam_api.so next to the binary and adds an absolute RPATH entry for
# it, so the RUNPATH "." stops mattering.
stdenv.mkDerivation {
  pname = "godotsteam";
  version = "g351-s155-gs3184";

  src = fetchurl {
    url = "https://codeberg.org/godotsteam/godotsteam/releases/download/g351-s155-gs3184/linux64-g351-s155-gs3184.zip";
    sha256 = "9debaa2a1f0a536cf168318ad768014d1873c5f346f281150162147d009fa6b5";
  };

  nativeBuildInputs = [
    unzip
    autoPatchelfHook
  ];

  # The X11 and GLX libraries the editor binary's NEEDED entries name.
  # libc, libpthread, libdl and libm come from glibc, already in every
  # Nix build closure; autoPatchelfHook resolves them without help.
  buildInputs = [
    xorg.libX11
    xorg.libXcursor
    xorg.libXinerama
    xorg.libXext
    xorg.libXrandr
    xorg.libXrender
    xorg.libXi
    libglvnd
  ];

  sourceRoot = ".";

  unpackPhase = ''
    unzip -q "$src"
  '';

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    mkdir -p "$out/opt/godotsteam"
    cp -v linux-351-editor.64 linux-351-template.64 linux-351-debug-template.64 \
      libsteam_api.so godotsteam.sh steam_appid.txt \
      "$out/opt/godotsteam/"
    chmod +x "$out/opt/godotsteam/linux-351-editor.64"
  '';

  meta.platforms = [ "x86_64-linux" ];
}
