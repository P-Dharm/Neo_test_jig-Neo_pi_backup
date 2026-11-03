$GAIN_HIGH = 224.47619
$GAIN_MID = 18.6428
$RS = 10.0
$OFFSET_HIGH = 117.78
$OFFSET_MID = 152.50

$test_points_5v = @(
    @(470, 10.417), @(503, 9.747), @(570, 8.621), @(590, 8.333),
    @(690, 7.143), @(910, 5.435), @(1010, 4.902), @(3313, 1.505),
    @(3862, 1.291), @(5100, 0.9785), @(10000, 0.500), @(15100, 0.331),
    @(33100, 0.151), @(38200, 0.131), @(43100, 0.116), @(47000, 0.106)
)

$test_points_3v6 = @(
    @(470, 7.500), @(503, 7.018), @(570, 6.207), @(590, 6.000),
    @(690, 5.143), @(910, 3.913), @(1010, 3.529), @(3313, 1.083),
    @(3862, 0.930), @(5100, 0.7045), @(10000, 0.360), @(15100, 0.238),
    @(33100, 0.109), @(38200, 0.094), @(43100, 0.084), @(47000, 0.077)
)

Write-Output "--- 5V Test Points ---"
foreach ($p in $test_points_5v) {
    $r = $p[0]
    $i = $p[1]
    $v_high = $i * $GAIN_HIGH * $RS + $OFFSET_HIGH
    $v_mid = $i * $GAIN_MID * $RS + $OFFSET_MID
    "{0,5} ohm | {1,7:F4} mA | Case3: {2,7:F1} mV | Case2: {3,7:F1} mV" -f $r, $i, $v_high, $v_mid
}

Write-Output "`n--- 3.6V Test Points ---"
foreach ($p in $test_points_3v6) {
    $r = $p[0]
    $i = $p[1]
    $v_high = $i * $GAIN_HIGH * $RS + $OFFSET_HIGH
    $v_mid = $i * $GAIN_MID * $RS + $OFFSET_MID
    "{0,5} ohm | {1,7:F4} mA | Case3: {2,7:F1} mV | Case2: {3,7:F1} mV" -f $r, $i, $v_high, $v_mid
}
