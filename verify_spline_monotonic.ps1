$nodes = @(
    @{ V=   0.0; Off=150.0 },
    @{ V= 332.0; Off=150.0 },
    @{ V= 352.0; Off=128.0 },
    @{ V= 365.0; Off=108.0 },
    @{ V= 430.0; Off=114.0 },
    @{ V= 550.0; Off=135.0 },
    @{ V=1150.0; Off=170.0 },
    @{ V=2500.0; Off=170.0 }
)

function Get-Calibrated-Offset($v) {
    if ($v -le $nodes[0].V) { return $nodes[0].Off }
    if ($v -ge $nodes[-1].V) { return $nodes[-1].Off }
    for ($i = 0; $i -lt ($nodes.Count - 1); $i++) {
        if ($v -ge $nodes[$i].V -and $v -le $nodes[$i+1].V) {
            $v0 = $nodes[$i].V
            $v1 = $nodes[$i+1].V
            $o0 = $nodes[$i].Off
            $o1 = $nodes[$i+1].Off
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
    $off = Get-Calibrated-Offset $v
    $i = ($v - $off) / $sens
    if ($i -lt $prev_i) {
        Write-Output ("NON-MONOTONIC at V={0:F2}: i={1:F4} < prev={2:F4}" -f $v, $i, $prev_i)
        $monotonic = $false
        break
    }
    $prev_i = $i
}

if ($monotonic) {
    Write-Output "SUCCESS: Spline transfer function is strictly monotonic across entire range (100 mV to 2300 mV)."
}
