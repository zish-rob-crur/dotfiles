$wsl_ipaddress = wsl -d "Ubuntu" hostname -I
$wsl_ipaddress1 = $wsl_ipaddress.Split(" ", 2)[0]
# print $wsl_ipaddress1
print "WSL IP Address: $wsl_ipaddress1"
$portMappings = @(
    2222,
    8080,
    11434
)

$listenaddress = "0.0.0.0"
$protocol = "v4tov4"

foreach ($port in $portMappings) {
    netsh interface portproxy delete $protocol listenaddress=$listenaddress listenport=$port
    netsh interface portproxy add $protocol listenaddress=$listenaddress listenport=$port connectaddress=$wsl_ipaddress1 connectport=$port
}
