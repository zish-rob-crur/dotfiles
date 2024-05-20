FROM nixos/nix:2.23.0pre20240519_7cb3c80

RUN nix-env -iA nixpkgs.cachix

RUN cachix use your-cachix-cache

RUN mkdir -p /etc/nix && echo 'sandbox = false' > /etc/nix/nix.conf

COPY shell.nix /workspace/
WORKDIR /workspace
RUN nix-shell /workspace/shell.nix --run "exit"

CMD ["zsh"]