{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
    buildInputs = [
        pkgs.neovim
        pkgs.git
        pkgs.zsh
        pkgs.conda
        pkgs.tmux
    ];
    shellHook = ''
    echo "Welcome to the development shell!"
    '';
}

