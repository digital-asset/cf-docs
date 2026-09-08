{ pkgs }:

let
  pythonBase = pkgs.python314;
  python = pythonBase.withPackages (ps: [
    ps.grpcio-tools
    ps.jinja2
    ps.mypy
    ps.protobuf
    ps.pytest
    ps.pyyaml
    ps.types-protobuf
    ps.types-pyyaml
  ]);
in
pkgs.mkShell {
  packages = [
    pkgs.bash
    pkgs.curl
    pkgs.gh
    pkgs.git
    pkgs.jdk25_headless
    pkgs.ncurses
    pkgs.nodejs_24
    pkgs.ruff
    pkgs.unzip
    python
  ];

  shellHook = ''
    export PATH="$PWD/node_modules/.bin:$HOME/.dpm/bin:$HOME/.daml/bin:$PATH"
    export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"

    case " $NODE_OPTIONS " in
      *" --max-old-space-size="*) ;;
      *)
        if [ -z "$NODE_OPTIONS" ]; then
          export NODE_OPTIONS="--max-old-space-size=12288"
        else
          export NODE_OPTIONS="$NODE_OPTIONS --max-old-space-size=12288"
        fi
        ;;
    esac

    if [ "''${SKIP_NPM_INSTALL:-}" != "1" ] && [ -f package.json ] && [ ! -d node_modules ]; then
      echo "Installing npm dependencies..."
      if [ -f package-lock.json ]; then
        npm ci
      else
        npm install
      fi
    fi
  '';
}
