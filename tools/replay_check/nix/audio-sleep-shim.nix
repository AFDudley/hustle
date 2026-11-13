{ stdenv, src }:

# Builds libaudiosleep.so from audio_sleep_shim.c. See README.md, "Audio
# faster than real time". Same compiler flags as the former Containerfile.
stdenv.mkDerivation {
  pname = "audio-sleep-shim";
  version = "1";

  inherit src;
  dontUnpack = true;

  buildPhase = ''
    runHook preBuild
    $CC -shared -fPIC -O2 -Wall -Werror -o libaudiosleep.so "$src" -ldl
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/opt/audio_sleep_shim"
    cp libaudiosleep.so "$out/opt/audio_sleep_shim/"
    runHook postInstall
  '';

  meta.platforms = [ "x86_64-linux" ];
}
