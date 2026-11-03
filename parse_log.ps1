$content = Get-Content 'oct-26-10-26_test_log.txt' -Raw
$pattern = '(?ms)TEST RESULTS: (BOARD \d).*?Final Current\s+:\s+([\d\.-]+)\s+mA.*?Active Range\s+:\s+(\w+)\s+\(Case (\d)\).*?Raw ADC Counts\s+:\s+(\d+).*?Raw ADC Voltage\s+:\s+([\d\.]+)\s+mV'
$regexMatches = [regex]::Matches($content, $pattern)
$results = foreach ($m in $regexMatches) {
    [PSCustomObject]@{
        Board   = $m.Groups[1].Value
        Current = [double]$m.Groups[2].Value
        Range   = $m.Groups[3].Value
        Case    = [int]$m.Groups[4].Value
        Counts  = [int]$m.Groups[5].Value
        Voltage = [double]$m.Groups[6].Value
    }
}
$results | Out-String -Width 120
