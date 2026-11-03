$lines = Get-Content 'C:\Users\dell\.gemini\antigravity-cli\brain\ea61cefa-c16f-4827-be59-da37da579768\.system_generated\logs\transcript.jsonl'
for ($i = $lines.Count - 1; $i -ge 0; $i--) {
    $obj = ConvertFrom-Json $lines[$i]
    if ($obj.source -eq 'MODEL' -and $obj.type -eq 'PLANNER_RESPONSE' -and $obj.content) {
        Write-Output ("Step " + $obj.step_index + ": " + $obj.content)
        break
    }
}
