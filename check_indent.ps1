$lines = Get-Content 'currentMeasurementModule.py'
$lineNum = 0
$hasTabSpaceIssue = $false
foreach ($line in $lines) {
    $lineNum++
    if ($line -match '^\t+ +[^\s*]') {
        Write-Output "Mixed tab/space at line $lineNum : $line"
        $hasTabSpaceIssue = $true
    }
}
if (-not $hasTabSpaceIssue) {
    Write-Output "No mixed tab/space issues found across all $($lines.Count) lines."
}
