$table = @(
    @{ R=690;    I_des=7.1429; V_adc=1500.95 },
    @{ R=910;    I_des=5.4348; V_adc=1184.931 },
    @{ R=1000;   I_des=4.9505; V_adc=1084.232 },
    @{ R=2000;   I_des=2.4876; V_adc= 602.991 },
    @{ R=2470;   I_des=2.0161; V_adc= 506.719 },
    @{ R=2910;   I_des=1.7123; V_adc= 444.834 },
    @{ R=2943;   I_des=1.6932; V_adc= 430.934 },
    @{ R=2959.9; I_des=1.6836; V_adc= 424.402 },
    @{ R=3300;   I_des=1.5106; V_adc= 397.076 },
    @{ R=3349.9; I_des=1.4881; V_adc= 391.092 },
    @{ R=3400;   I_des=1.4663; V_adc= 380.991 },
    @{ R=3520;   I_des=1.4164; V_adc= 370.041 },
    @{ R=3640;   I_des=1.3699; V_adc= 361.270 },
    @{ R=3803;   I_des=1.3113; V_adc= 358.372 },
    @{ R=3990;   I_des=1.2500; V_adc= 354.472 },
    @{ R=4110;   I_des=1.2136; V_adc= 351.867 },
    @{ R=4143;   I_des=1.2039; V_adc= 352.168 },
    @{ R=4159.9; I_des=1.1991; V_adc= 351.930 },
    @{ R=4210;   I_des=1.1848; V_adc= 351.606 },
    @{ R=5100;   I_des=0.9785; V_adc= 332.221 }
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

Write-Output "--- Testing Optimized Nodes ---"
$max_err = 0.0
foreach ($p in $table) {
    $v = $p.V_adc
    $off = Get-Calibrated-Offset-Opt $v
    $new_meas = ($v - $off) / $sens
    $new_err = [Math]::Abs($new_meas - $p.I_des) / $p.I_des * 100.0
    if ($new_err -gt $max_err) { $max_err = $new_err }
    "{0,7} | Des={1,7:F4} | V_adc={2,7:F2} | Off={3,6:F2} | New={4,7:F4} | Err={5,5:F2}%" -f `
        $p.R, $p.I_des, $v, $off, $new_meas, $new_err
}
Write-Output ("`nMAX ERROR = {0:F2}%" -f $max_err)
