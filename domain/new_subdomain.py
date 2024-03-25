import argparse
import os
from re import sub
import subprocess
from pathlib import Path


acme_sh_path = Path("~/.acme.sh/acme.sh")

available_base_path = "/etc/nginx/sites-available"
enabled_base_path = "/etc/nginx/sites-enabled"
ssl_base_path = "/etc/nginx/ssl"


def check_permissions():

    # check available and enabled directories
    if not Path(available_base_path).exists():
        print(f"{available_base_path} not found.")
        exit(1)
    else:
        print(f"{available_base_path} found. checking permissions...")
        if not os.access(available_base_path, os.W_OK):
            print(f"No write permission for {available_base_path}")
            exit(1)

    if not Path(enabled_base_path).exists():
        print(f"{enabled_base_path} not found.")
        exit(1)
    else:
        print(f"{enabled_base_path} found. checking permissions...")
        if not os.access(enabled_base_path, os.W_OK):
            print(f"No write permission for {enabled_base_path}")
            exit(1)

    if not Path(ssl_base_path).exists():
        print(f"{ssl_base_path} not found.")
        exit(1)
    else:
        print(f"{ssl_base_path} found. checking permissions...")
        if not os.access(ssl_base_path, os.W_OK):
            print(f"No write permission for {ssl_base_path}")
            exit(1)


def create_nginx_config(domain, proxy):
    config_template = f"""
server {{
    listen 80;
    server_name {domain};

    root /var/www/html;
    index index.html index.htm;

    location / {{
        try_files $uri $uri/ =404;
    }}

    location ~ /.well-known/acme-challenge {{
        allow all;
        root /var/www/html;
    }}
}}

"""

    available_path = f"{available_base_path}/{domain}"
    enabled_path = f"{enabled_base_path}/{domain}"
    with open(available_path, "w") as file:
        file.write(config_template)

    # Creating symlink
    if not os.path.islink(enabled_path):
        os.symlink(available_path, enabled_path)
        print(f"Symlink created for {domain}")


def update_nginx_config(domain, proxy):
    config = f"""
    server {{
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
        proxy_pass http://{proxy};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }}
}}
"""
    config_path = f"{enabled_base_path}/{domain}"
    with open(config_path, "w") as file:
        file.write(config)
    print(f"Updated Nginx config for {domain}")


def reload_nginx():
    os.system("sudo nginx -t && sudo nginx -s reload")
    print("Nginx reloaded")


def issue_certificate(domain):
    os.system(f"{acme_sh_path} --issue -d {domain} --webroot /var/www/html -k ec-256")
    print("Certificate issued")


def install_certificate(domain):
    nginx_ssl_path = f"{ssl_base_path}/{domain}"
    os.makedirs(nginx_ssl_path, exist_ok=True)
    os.system(
        f"""
{acme_sh_path} --install-cert -d {domain} \\
    --cert-file {nginx_ssl_path}/cert.pem \\
    --key-file {nginx_ssl_path}/key.pem \\
    --fullchain-file {nginx_ssl_path}/fullchain.pem \\
    --reloadcmd "sudo systemctl reload nginx"
"""
    )
    print("Certificate installed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Setup Nginx subdomain with SSL and proxy."
    )
    parser.add_argument(
        "--domain",
        type=str,
        required=True,
        help="Domain name for the Nginx configuration.",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        required=True,
        help="Proxy pass destination, e.g., http://localhost:3000.",
    )

    args = parser.parse_args()
    check_permissions()
    create_nginx_config(args.domain, args.proxy)
    reload_nginx()
    issue_certificate(args.domain)
    install_certificate(args.domain)
    update_nginx_config(args.domain, args.proxy)
    print("Nginx setup and SSL certificate installation completed.")
