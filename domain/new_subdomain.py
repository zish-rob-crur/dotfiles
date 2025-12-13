#!/usr/bin/env python3
import argparse
import os
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

available_base_path = Path("/etc/nginx/sites-available")
enabled_base_path = Path("/etc/nginx/sites-enabled")
ssl_base_path = Path("/etc/nginx/ssl")
default_webroot = Path("/var/www/html")


def _sudo_prefix() -> list[str]:
    return [] if os.geteuid() == 0 else ["sudo"]


def _run(cmd: list[str], *, input_text: Optional[str] = None) -> None:
    subprocess.run(
        cmd,
        input=input_text,
        text=input_text is not None,
        check=True,
    )


def _validate_domain(domain: str) -> str:
    domain = domain.strip()
    if not domain or any(ch.isspace() for ch in domain):
        raise SystemExit("Invalid --domain: must be a non-empty hostname without spaces.")
    if "://" in domain or "/" in domain:
        raise SystemExit("Invalid --domain: do not include scheme or path.")
    if ":" in domain:
        raise SystemExit("Invalid --domain: do not include a port.")
    return domain


def _normalize_proxy(proxy: str) -> str:
    proxy = proxy.strip()
    if not proxy:
        raise SystemExit("Invalid --proxy: must be non-empty.")
    parsed = urlparse(proxy)
    if parsed.scheme:
        return proxy
    return f"http://{proxy}"


def _acme_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(os.path.expanduser(f"~{sudo_user}"))
    return Path.home()


def _acme_sh_path(cli_value: Optional[str]) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    return _acme_user_home() / ".acme.sh" / "acme.sh"


def _acme_home_dir(acme_sh: Path) -> Path:
    return acme_sh.parent


def check_prerequisites(acme_sh: Path, webroot: Path) -> None:
    for path in (available_base_path, enabled_base_path):
        if not path.exists():
            raise SystemExit(f"{path} not found.")
    if not webroot.exists():
        raise SystemExit(f"Webroot not found: {webroot}")
    if not acme_sh.exists():
        raise SystemExit(f"acme.sh not found at: {acme_sh} (use --acme-sh to override)")


def _write_root_file(path: Path, content: str) -> None:
    subprocess.run(
        _sudo_prefix() + ["tee", str(path)],
        input=content,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _ensure_dir(path: Path) -> None:
    _run(_sudo_prefix() + ["mkdir", "-p", str(path)])


def _remove_path(path: Path) -> None:
    _run(_sudo_prefix() + ["rm", "-f", str(path)])


def _ensure_symlink(target: Path, link_path: Path, *, force: bool) -> None:
    if force:
        _run(_sudo_prefix() + ["ln", "-sfn", str(target), str(link_path)])
        return

    if link_path.is_symlink():
        if link_path.resolve() == target.resolve():
            return
        raise SystemExit(f"{link_path} already points to {link_path.resolve()} (use --force to replace).")
    if link_path.exists():
        raise SystemExit(f"{link_path} already exists (use --force to replace).")

    _run(_sudo_prefix() + ["ln", "-s", str(target), str(link_path)])


def create_nginx_config(domain: str, webroot: Path, *, force: bool) -> None:
    config_template = f"""server {{
    listen 80;
    server_name {domain};

    root {webroot};
    index index.html index.htm;

    location / {{
        try_files $uri $uri/ =404;
    }}

    location ^~ /.well-known/acme-challenge/ {{
        allow all;
        root {webroot};
    }}
}}
"""
    available_path = available_base_path / domain
    enabled_path = enabled_base_path / domain

    if available_path.exists() and not force:
        raise SystemExit(f"{available_path} already exists. Use --force to overwrite.")
    if available_path.exists() and force:
        _remove_path(available_path)

    _write_root_file(available_path, config_template)
    _ensure_symlink(available_path, enabled_path, force=force)


def update_nginx_config(domain: str, proxy: str, *, force: bool) -> None:
    proxy_url = _normalize_proxy(proxy)
    config = f"""server {{
    listen 80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate {ssl_base_path}/{domain}/fullchain.pem;
    ssl_certificate_key {ssl_base_path}/{domain}/key.pem;
    ssl_session_timeout 1d;
    ssl_session_cache shared:MozSSL:10m;
    ssl_session_tickets off;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    location / {{
        proxy_pass {proxy_url};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }}
}}
"""
    available_path = available_base_path / domain
    if available_path.exists() and not force:
        raise SystemExit(f"{available_path} already exists. Use --force to overwrite.")
    if available_path.exists() and force:
        _remove_path(available_path)

    _write_root_file(available_path, config)


def reload_nginx() -> None:
    _run(_sudo_prefix() + ["nginx", "-t"])
    _run(_sudo_prefix() + ["nginx", "-s", "reload"])


def issue_certificate(acme_sh: Path, domain: str, webroot: Path) -> None:
    _run(
        _sudo_prefix()
        + [
            str(acme_sh),
            "--home",
            str(_acme_home_dir(acme_sh)),
            "--issue",
            "-d",
            domain,
            "--webroot",
            str(webroot),
            "-k",
            "ec-256",
        ]
    )


def install_certificate(acme_sh: Path, domain: str) -> None:
    nginx_ssl_path = ssl_base_path / domain
    _ensure_dir(nginx_ssl_path)
    _run(
        _sudo_prefix()
        + [
            str(acme_sh),
            "--home",
            str(_acme_home_dir(acme_sh)),
            "--install-cert",
            "-d",
            domain,
            "--cert-file",
            str(nginx_ssl_path / "cert.pem"),
            "--key-file",
            str(nginx_ssl_path / "key.pem"),
            "--fullchain-file",
            str(nginx_ssl_path / "fullchain.pem"),
            "--reloadcmd",
            "systemctl reload nginx",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup Nginx domain with SSL and reverse proxy.")
    parser.add_argument("--domain", type=str, required=True, help="Domain for the Nginx server_name.")
    parser.add_argument(
        "--proxy",
        type=str,
        required=True,
        help="Upstream for proxy_pass, e.g. localhost:3000 or http://localhost:3000.",
    )
    parser.add_argument(
        "--webroot",
        type=str,
        default=str(default_webroot),
        help="Webroot used for ACME http-01 challenge (default: /var/www/html).",
    )
    parser.add_argument(
        "--acme-sh",
        type=str,
        default=None,
        help="Path to acme.sh (default: ~/.acme.sh/acme.sh; under sudo, uses $SUDO_USER home).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing nginx config / symlink.")

    args = parser.parse_args()
    domain = _validate_domain(args.domain)
    acme_sh = _acme_sh_path(args.acme_sh)
    webroot = Path(args.webroot)

    check_prerequisites(acme_sh, webroot)

    create_nginx_config(domain, webroot, force=args.force)
    reload_nginx()
    issue_certificate(acme_sh, domain, webroot)
    install_certificate(acme_sh, domain)
    update_nginx_config(domain, args.proxy, force=args.force)
    reload_nginx()
    print("Nginx setup and SSL certificate installation completed.")


if __name__ == "__main__":
    main()
