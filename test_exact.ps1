$table = @(
    @{ R="690";    I_des=7.1429; V_adc=1500.95 },
    @{ R="910";    I_des=5.4348; V_adc=1184.931 },
    @{ R="1000";   I_des=4.9505; V_adc=1084.232 },
    @{ R="2000";   I_des=2.4876; V_adc= 602.991 },
    @{ R="2470";   I_des=2.0161; V_adc= 506.719 },
    @{ R="2910";   I_des=1.7123; V_adc= 444.834 },
    @{ R="2943";   I_des=1.6932; V_adc= 430.934 },
    @{ R="2959.9"; I_des=1.6836; V_adc= 424.402 },
    @{ R="3300";   I_des=1.5106; V_adc= 396.757 },  # User's exact new test: 396.757 mV
    @{ R="3349.9"; I_des=1.4881; V_adc= 391.092 },
    @{ R="3400";   I_des=1.4663; V_adc= 380.991 },
    @{ R="3520";   I_des=1.4164; V_adc= 370.041 },
    @{ R="3640";   I_des=1.3699; V_adc= 361.270 },
    @{ R="3803";   I_des=1.3113; V_adc= 358.372 },
    @{ R="3990";   I_des=1.2500; V_adc= 354.472 },
    @{ R="4110";   I_des=1.2136; V_adc= 351.867 },
    @{ R="4143";   I_des=1.2039; V_adc= 352.168 },
    @{ R="4159.9"; I_des=1.1991; V_adc= 351.930 },
    @{ R="4210";   I_des=1.1848; V_adc= 351.606 },
    @{ R="5100";   I_des=0.9785; V_adc= 332.221 }
)

$nodes_exact = @(
    @{ V=   0.0; Off=150.0 },
    @{ V= 332.0; Off=149.8 },
    @{ V= 352.0; Off=128.0 },
    @{ V= 360.0; Off=106.0 },
    @{ V= 370.0; Off=106.0 },
    @{ V= 381.0; Off=107.6 },
    @{ V= 391.0; Off=113.7 },
    @{ V= 397.0; Off=115.1 },
    @{ V= 430.0; Off=114.5 },
    @{ V= 445.0; Off=125.6 },
    @{ V= 507.0; Off=130.8 },
    @{ V= 603.0; Off=139.2 },
    @{ V=1084.0; Off=161.3 },
    @{ V=1185.0; Off=171.7 },
    @{ V=1501.0; Off=169.3 },
    @{ V=2500.0; Off=169.3 }
)

function Get-Offset-Exact($v) {
    if ($v -le $nodes_exact[0].V) { return $nodes_exact[0].Off }
    if ($v -ge $nodes_exact[-1].V) { return $nodes_exact[-1].Off }
    for ($i = 0; $i -lt ($nodes_exact.Count - 1); $i++) {
        if ($v -ge $nodes_exact[$i].V -and $v -le $nodes_exact[$i+1].V) {
            $v0 = $nodes_exact[$i].V
            $v1 = $nodes_exact[$i+1].V
            $o0 = $nodes_exact[$i].Off
            $o1 = $nodes_exact[$i+1].Off
            $t = ($v - $v0) / ($v1 - $v0)
            return $o0 + $t * ($o1 - $o0)
        }
    }
    return 117.78
}

$sens = 18.6428 * 10.0

Write-Output "=========================================================================================================="
Write-Output ("{0,7} | {1,7} | {2,8} | {3,7} | {4,8} | {5,7}" -f "Res", "I_des", "V_adc", "Off", "Current", "Error%")
Write-Output "=========================================================================================================="

$max_err = 0.0
foreach ($p in $table) {
    $v = $p.V_adc
    $off = Get-Offset-Exact $v
    $i = ($v - $off) / $sens
    $err = [Math]::Abs($i - $p.I_des) / $p.I_des * 100.0
    if ($err -gt $max_err) { $max_err = $err }
    Write-Output ("{0,7} | {1,7:F4} | {2,8:F3} | {3,7:F2} | {4,8:F4} | {5,6:F2}%" -f `
        $p.R, $p.I_des, $v, $off, $i, $err)
}

Write-Output ("`nMAX ERROR ACROSS ALL BENCH POINTS: {0:F2}%" -f $max_err)
