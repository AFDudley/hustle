{ dockerTools, fgobjectDiff }:

# The FGObject diff image. Holds only the fgobject_diff binary (built
# by ./rust.nix from the committed Cargo.lock): the binary itself does
# the comparison and writes the report, so this image needs no
# Python. See ../README.md, "The container".
dockerTools.buildLayeredImage {
  name = "localhost/hustle-fgobject-diff";
  tag = "latest";

  contents = [
    fgobjectDiff
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
