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

Write-Output "--- Testing 3.6V with Optimized Nodes ---"
foreach ($p in $points_3v6) {
    $v = $p.V_adc
    $off = Get-Calibrated-Offset-Opt $v
    $new_meas = ($v - $off) / $sens
    $new_err = [Math]::Abs($new_meas - $p.I_des) / $p.I_des * 100.0
    "{0,5} ohm | Des={1,7:F4} | V_adc={2,7:F2} | Off={3,6:F2} | New={4,7:F4} | Err={5,5:F2}%" -f `
        $p.R, $p.I_des, $v, $off, $new_meas, $new_err
}
