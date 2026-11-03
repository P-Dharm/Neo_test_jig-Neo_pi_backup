$points_3v6 = @(
    @{ R=470;  I_des=7.5000; V_adc=1589.60 },
    @{ R=503;  I_des=7.0175; V_adc=1455.90 },
    @{ R=570;  I_des=6.2069; V_adc=1331.70 },
    @{ R=590;  I_des=6.0000; V_adc=1291.80 },
    @{ R=690;  I_des=5.1429; V_adc=1122.50 },
    @{ R=910;  I_des=3.9130; V_adc= 876.80 },
    @{ R=1000; I_des=3.5644; V_adc= 811.20 },
    @{ R=3300; I_des=1.0876; V_adc= 346.35 },
    @{ R=3881; I_des=0.9252; V_adc= 316.00 }
)

$gain = 18.6428
$rs = 10.0
$sens = $gain * $rs

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

Write-Output "=== 3.6V Points with Spline Calibration ==="
foreach ($p in $points_3v6) {
    $v = $p.V_adc
    $off = Get-Calibrated-Offset $v
    $i = ($v - $off) / $sens
    $err = [Math]::Abs($i - $p.I_des) / $p.I_des * 100.0
    "{0,5} ohm | I_des={1,7:F4} mA | V_adc={2,7:F2} mV | Off={3,6:F2} mV | Calc={4,7:F4} mA | Err={5,5:F2}%" -f `
        $p.R, $p.I_des, $v, $off, $i, $err
}
