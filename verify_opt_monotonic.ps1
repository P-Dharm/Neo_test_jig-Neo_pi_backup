$nodes_opt = @(
    @{ V=   0.0; Off=150.0 },
    @{ V= 332.0; Off=150.0 },
    @{ V= 352.0; Off=128.0 },
    @{ V= 360.0; Off=106.0 },
    @{ V= 385.0; Off=108.0 },
    @{ V= 430.0; Off=114.0 },
    @{ V= 450.0; Off=124.0 },
    @{ V= 550.0; Off=135.0 },
    @{ V=1150.0; Off=170.0 },
    @{ V=2500.0; Off=170.0 }
)

function Get-Calibrated-Offset-Opt($v) {
    if ($v -le $nodes_opt[0].V) { return $nodes_opt[0].Off }
    if ($v -ge $nodes_opt[-1].V) { return $nodes_opt[-1].Off }
    for ($i = 0; $i -lt ($nodes_opt.Count - 1); $i++) {
        if ($v -ge $nodes_opt[$i].V -and $v -le $nodes_opt[$i+1].V) {
            $v0 = $nodes_opt[$i].V
            $v1 = $nodes_opt[$i+1].V
            $o0 = $nodes_opt[$i].Off
            $o1 = $nodes_opt[$i+1].Off
            $t = ($v - $v0) / ($v1 - $v0)
            return $o0 + $t * ($o1 - $o0)
        }
    }
    return 152.50
}

$sens = 18.6428 * 10.0
$prev_i = -100.0
$monotonic = $true

for ($v = 100.0; $v -le 2300.0; $v += 0.2) {
    $off = Get-Calibrated-Offset-Opt $v
    $i = ($v - $off) / $sens
    if ($i -lt $prev_i) {
        Write-Output ("NON-MONOTONIC at V={0:F2}: i={1:F4} < prev={2:F4}" -f $v, $i, $prev_i)
        $monotonic = $false
        break
    }
    $prev_i = $i
}

if ($monotonic) {
    Write-Output "SUCCESS: Strictly monotonic across 100 mV to 2300 mV."
}
