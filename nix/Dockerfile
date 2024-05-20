FROM nixos/nix:2.23.0pre20240519_7cb3c80

RUN nix-env -iA nixpkgs.nix
RUN mkdir -p /etc/nix && echo 'sandbox = false' > /etc/nix/nix.conf
COPY shell.nix /workspace/
WORKDIR /workspace
RUN nix-shell --run "exit"
CMD ["nix-shell"]