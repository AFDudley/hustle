{
  pkgs,
  lib,
  dockerTools,
  godotsteam,
  audioSleepShim,
  netsim,
}:

# The replay check image. See README.md, "The container".
#
# Four hard points, solved here:
#
# 1. linux-351-editor.64 is a prebuilt glibc ELF. godotsteam.nix patches
#    its interpreter and RPATH with autoPatchelfHook.
# 2. lib/tbfg.so, the game's own native library, is a prebuilt glibc ELF
#    too, and run.py never patches it: it is loaded from the project copy
#    at run time through GDNative. Its NEEDED libraries (libgcc_s.so.1,
#    libc.so.6) must resolve through the ordinary dynamic-linker search,
#    so LD_LIBRARY_PATH below includes glibc's and libgcc's Nix store
#    paths.
# 3. Without --gpu, Godot draws through GLX straight to Xvfb, using
#    Mesa's software driver (llvmpipe). Two things must be true for
#    that. First, Xvfb must carry GLX: nixpkgs' own xorg.xvfb is a
#    trimmed build with no Mesa and no GLX at all, so xvfbWithGlx below
#    takes the Xvfb binary from the full build, xorg.xorgserver, instead
#    (xvfbRunWithGlx does the same for tools/botplay's xvfb-run). This is
#    a local override through each package's own `xorg` argument, not a
#    nixpkgs-wide overlay: overriding xorg.xvfb globally changes the
#    identity of the whole xorg attribute set, which forces unrelated
#    packages that take `xorg` as a build input (gtk-doc's toolchain, by
#    observation) to rebuild from source.
#    Second, the X server's software GLX provider must find
#    swrast_dri.so. The X server looks for it at a fixed, compiled-in
#    path, /run/opengl-driver/lib/dri (nixpkgs' convention for a
#    directory a non-NixOS image must populate itself), so extraCommands
#    below links that path to this image's own Mesa. swrast_dri.so then
#    needs libEGL.so.1 itself, so LD_LIBRARY_PATH carries libglvnd too.
# 4. With --gpu, VirtualGL's EGL back end (`vglrun -d egl`) renders on
#    the GPU the NVIDIA Container Toolkit (CDI) passes in, and copies
#    each frame into the Xvfb window over GLX (still Mesa, as in point
#    3). __EGL_VENDOR_LIBRARY_DIRS carries Mesa's own EGL vendor file and
#    the directory CDI mounts the NVIDIA EGL vendor file into, at
#    /usr/share/glvnd/egl_vendor.d. The NVIDIA user-space libraries land
#    at /usr/lib/x86_64-linux-gnu through the same CDI mount, so that
#    directory is in LD_LIBRARY_PATH too.
let
  libPath = lib.makeLibraryPath [
    pkgs.mesa
    pkgs.libglvnd
    pkgs.stdenv.cc.cc.lib
    pkgs.glibc
    # Godot dlopens libudev.so.1 for its joypad monitor. Without it, Godot
    # 3.5.1 falls back to polling /dev/input in JoypadLinux::monitor_joypads,
    # whose usleep(1000000) sits outside its while loop. That thread then
    # spins on openat("/dev/input") for the whole run: one busy core per
    # Godot process (seen with strace and gdb in a 32-process sweep).
    # libudev-zero gives the udev path, which blocks in select() instead.
    pkgs.libudev-zero
  ];

  xvfbWithGlx = pkgs.runCommand "xvfb-with-glx" { } ''
    mkdir -p "$out/bin"
    ln -s "${pkgs.xorg.xorgserver}/bin/Xvfb" "$out/bin/Xvfb"
  '';

  xvfbRunWithGlx = pkgs.xvfb-run.override {
    xorg = pkgs.xorg // {
      xvfb = xvfbWithGlx;
    };
  };

  # ffmpeg and ffprobe without inherited LD_PRELOAD. A game started with
  # vglrun passes VirtualGL's preloads (libdlfaker.so, libvglfaker.so) to
  # every child. YOMIRecord starts ffmpeg from inside the game, and with
  # libdlfaker.so preloaded ffmpeg printed "Failed loading SDL3 library."
  # and listed nothing. `vglrun -nodl` would spare ffmpeg but breaks the
  # game's GL: glxinfo failed with "undefined symbol: eglGetProcAddress".
  # ffmpeg needs no preload (neither VirtualGL's nor the audio shim's).
  ffmpegNoPreload = pkgs.runCommand "ffmpeg-no-preload" { } ''
    mkdir -p "$out/bin"
    for tool in ffmpeg ffprobe; do
      printf '#!${pkgs.bash}/bin/bash\nunset LD_PRELOAD\nexec ${pkgs.ffmpeg}/bin/%s "$@"\n' "$tool" > "$out/bin/$tool"
      chmod +x "$out/bin/$tool"
    done
  '';
in
dockerTools.buildLayeredImage {
  name = "localhost/hustle-replay-check";
  tag = "latest";

  contents = [
    pkgs.bashInteractive
    pkgs.coreutils
    pkgs.gnugrep
    # vglrun rebuilds LD_PRELOAD with sed. Without sed the result was empty,
    # so every vglrun launch silently dropped the audio shim
    # (libaudiosleep.so) and lockstep audio fell back to real time.
    pkgs.gnused
    pkgs.gawk
    pkgs.glxinfo
    pkgs.inetutils # hostname: vglrun's wrapper script calls it
    pkgs.python3
    pkgs.rsync
    xvfbWithGlx
    xvfbRunWithGlx
    pkgs.xorg.xauth
    ffmpegNoPreload
    pkgs.virtualgl
    pkgs.mesa
    pkgs.libglvnd
    # godotsteam is not in contents: extraCommands copies it (see below).
    audioSleepShim
    netsim # udp-netsim: latency and loss relay for network tests
  ];

  # /opt/godotsteam must be a real directory, not a link into the store.
  # The game's ModLoader reads mods from <executable dir>/mods
  # (OS.get_executable_path().get_base_dir()). Through a store link, that
  # resolved to the read-only /nix/store/...-godotsteam-.../opt/godotsteam,
  # so a test could not add mods and the YOMIRecord e2e driver never
  # loaded. The copies keep their patched interpreter and RPATH.
  extraCommands = ''
    mkdir -p tmp
    chmod 1777 tmp
    mkdir -p root
    chmod 0700 root
    mkdir -p usr/lib/x86_64-linux-gnu
    mkdir -p usr/share/glvnd/egl_vendor.d
    mkdir -p run/opengl-driver/lib
    ln -s "${pkgs.mesa}/lib/dri" run/opengl-driver/lib/dri
    mkdir -p opt/godotsteam/mods
    cp -L ${godotsteam}/opt/godotsteam/* opt/godotsteam/
    chmod u+w opt/godotsteam/*
  '';

  config = {
    Env = [
      "PATH=/bin"
      "HOME=/root"
      "LD_LIBRARY_PATH=/opt/godotsteam:${libPath}:/usr/lib/x86_64-linux-gnu"
      "__EGL_VENDOR_LIBRARY_DIRS=/usr/share/glvnd/egl_vendor.d:${pkgs.mesa}/share/glvnd/egl_vendor.d"
      "GODOT_HOME=/opt/godotsteam"
      "AUDIO_SLEEP_SHIM=/opt/audio_sleep_shim/libaudiosleep.so"
      "REPLAY_CHECK_CONTAINER=1"
    ];
    Entrypoint = [
      "python3"
      "/repo/tools/replay_check/run.py"
    ];
  };
}
