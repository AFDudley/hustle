{ dockerTools, tableDump }:

# The table_dump image: just the one binary, built by ./rust.nix. See
# ../README.md, "The container".
dockerTools.buildLayeredImage {
  name = "localhost/hustle-table-dump";
  tag = "latest";

  contents = [ tableDump ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
