<#
  Model-to-Gradio diagnostic workflow (read-only except for the .\diagnostics folder it creates).
  Does not install anything and does not change global or project configuration.

  Run:  powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Model To Gradio\scripts\diagnose_rea.ps1"
  Option: -SkipModelRun   skips the one real OpenCode model run (step 3c), e.g. to avoid spending model credits.
#>
param([switch]$SkipModelRun)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$diag = Join-Path $root 'diagnostics'
New-Item -ItemType Directory -Force -Path $diag | Out-Null
$summaryPath = Join-Path $diag 'SUMMARY.txt'
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($summaryPath, ("M2G diagnostics " + (Get-Date -Format 's') + "`r`nWorking directory: $root`r`n"), $utf8)

function Say([string]$m) { [IO.File]::AppendAllText($summaryPath, $m + "`r`n", $utf8); Write-Host $m }
function Redact([string]$t) {
    if (-not $t) { return '' }
    [regex]::Replace($t, '(?i)\b(sk-[A-Za-z0-9_\-]{16,}|hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,})', '[REDACTED]')
}
function Save([string]$name, [string]$text) { [IO.File]::WriteAllText((Join-Path $diag $name), (Redact $text), $utf8) }
function Q([string]$a) { if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a } }

# Runs one non-interactive process (shell not used), stdin closed and stdout/stderr captured.
# A null TimeoutSec waits for the process to finish; this is used for the OpenCode model run.
function Invoke-Capture {
    param([string]$File, [string]$Arguments, [Nullable[int]]$TimeoutSec = 120, [hashtable]$EnvVars = @{})
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $File; $psi.Arguments = $Arguments; $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    foreach ($k in $EnvVars.Keys) { $psi.EnvironmentVariables[$k] = [string]$EnvVars[$k] }
    $r = [ordered]@{ ExitCode = $null; Stdout = ''; Stderr = ''; TimedOut = $false; StartError = $null }
    try { $p = [Diagnostics.Process]::Start($psi) } catch { $r.StartError = $_.Exception.Message; return [pscustomobject]$r }
    $p.StandardInput.Close()
    $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
    if ($null -eq $TimeoutSec) {
        $p.WaitForExit()
    } elseif (-not $p.WaitForExit($TimeoutSec * 1000)) {
        $r.TimedOut = $true
        & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null
        [void]$p.WaitForExit(5000)
    }
    $r.Stdout = $o.GetAwaiter().GetResult()
    $r.Stderr = $e.GetAwaiter().GetResult()
    try { $r.ExitCode = $p.ExitCode } catch { }
    [pscustomobject]$r
}
function Describe-Result($res) {
    if ($res.StartError) { return "could not start: $($res.StartError)" }
    if ($res.TimedOut) { return "TIMED OUT (killed); exit code $($res.ExitCode)" }
    "exit code $($res.ExitCode)"
}

# ------------------------------------------------------------------------------------------------ STEP 1
Say ''
Say '=== STEP 1: static check of the changed files (tests are NOT executed in this step) ==='
$v = Get-Content -Raw -LiteralPath (Join-Path $root 'm2g\validate.py')
$g = Get-Content -Raw -LiteralPath (Join-Path $root 'm2g\gradio_gen.py')
$t = Get-Content -Raw -LiteralPath (Join-Path $root 'tests\test_call_arguments.py')
Say ("validate.py defines check_call_arguments:                  " + [bool]($v -match 'def check_call_arguments'))
Say ("validate_app_code calls check_call_arguments(tree):       " + [bool]($v -match '(?s)def validate_app_code.*check_call_arguments\(tree\)'))
Say ("gradio_gen.py request contains the 'exactly once' rule:   " + [bool]($g -match 'exactly once'))
Say ("tests/test_call_arguments.py test functions found:        " + ([regex]::Matches($t, '(?m)^def test_')).Count + ' (expected 8)')

# ------------------------------------------------------------------------------------------------ STEP 2
Say ''
Say '=== STEP 2: REA MCP server: initialize + tools/list over stdio (command taken from opencode.json) ==='
$all = New-Object Collections.ArrayList
$init = $null
$tx = New-Object Text.StringBuilder
$p = $null
try {
    $cfg = Get-Content -Raw -LiteralPath (Join-Path $root 'opencode.json') | ConvertFrom-Json
    $cmd = @($cfg.mcp.rea.command)
    $reaFile = [string]$cmd[0]
    $reaArgs = (($cmd | Select-Object -Skip 1) | ForEach-Object { Q ([string]$_) }) -join ' '
    Say "REA command from opencode.json: $reaFile $reaArgs"

    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $reaFile; $psi.Arguments = $reaArgs; $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $p = [Diagnostics.Process]::Start($psi)
    $errTask = $p.StandardError.ReadToEndAsync()
    $script:lineTask = $null

    function Send-Msg($obj) {
        $json = $obj | ConvertTo-Json -Depth 20 -Compress
        [void]$tx.AppendLine('>> ' + $json)
        $bytes = $utf8.GetBytes($json + "`n")      # newline-delimited JSON-RPC, UTF-8 without BOM
        $s = $p.StandardInput.BaseStream
        $s.Write($bytes, 0, $bytes.Length); $s.Flush()
    }
    function Read-Reply([int]$id, [int]$timeoutSec) {
        $deadline = (Get-Date).AddSeconds($timeoutSec)
        while ($true) {
            $left = [int]($deadline - (Get-Date)).TotalMilliseconds
            if ($left -le 0) { return $null }
            if ($null -eq $script:lineTask) { $script:lineTask = $p.StandardOutput.ReadLineAsync() }
            if (-not $script:lineTask.Wait($left)) { return $null }
            $line = $script:lineTask.Result; $script:lineTask = $null
            if ($null -eq $line) { [void]$tx.AppendLine('<< (server closed stdout)'); return $null }
            [void]$tx.AppendLine('<< ' + $line)
            try { $m = $line | ConvertFrom-Json } catch { continue }
            $names = @($m.PSObject.Properties.Name)
            if (($names -contains 'id') -and ($m.id -eq $id) -and -not ($names -contains 'method')) { return $m }
        }
    }

    Send-Msg ([ordered]@{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = [ordered]@{
        protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = [ordered]@{ name = 'm2g-diagnostic'; version = '0' } } })
    $init = Read-Reply 1 90
    if ($null -eq $init) { Say 'initialize: NO REPLY within 90 s (see rea_mcp_transcript.txt / rea_stderr.txt)' }
    elseif ($init.error) { Say ("initialize: SERVER ERROR " + ($init.error | ConvertTo-Json -Compress -Depth 10)) }
    else {
        Say ("initialize: OK, server=" + ($init.result.serverInfo | ConvertTo-Json -Compress) + " protocolVersion=" + $init.result.protocolVersion)
        Send-Msg ([ordered]@{ jsonrpc = '2.0'; method = 'notifications/initialized' })
        $cursor = $null; $id = 2
        for ($page = 1; $page -le 50; $page++) {
            if ($cursor) { $prm = @{ cursor = $cursor } } else { $prm = @{} }
            Send-Msg ([ordered]@{ jsonrpc = '2.0'; id = $id; method = 'tools/list'; params = $prm })
            $rep = Read-Reply $id 90
            if ($null -eq $rep) { Say "tools/list page ${page}: NO REPLY"; break }
            if ($rep.error) { Say ("tools/list page ${page}: SERVER ERROR " + ($rep.error | ConvertTo-Json -Compress -Depth 10)); break }
            foreach ($tool in @($rep.result.tools)) { [void]$all.Add($tool) }
            $cursor = $rep.result.nextCursor; $id++
            if (-not $cursor) { break }
        }
        Say "tools/list: $($all.Count) tool(s) received"
    }
}
catch { Say ("STEP 2 failed: " + $_.Exception.Message) }
finally {
    if ($p -and -not $p.HasExited) {
        try { $p.StandardInput.Close() } catch { }
        & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null
    }
    $stderrText = ''
    if ($errTask -and $errTask.Wait(3000)) { $stderrText = $errTask.Result }
    Save 'rea_stderr.txt' $stderrText
    Save 'rea_mcp_transcript.txt' $tx.ToString()
}
if ($all.Count -gt 0) {
    Save 'rea_tools.json' (ConvertTo-Json -InputObject @($all) -Depth 100)
    $fence = '```'
    $md = New-Object Text.StringBuilder
    [void]$md.AppendLine("# REA MCP tools/list: $($all.Count) tools")
    if ($init) {
        [void]$md.AppendLine("server: " + ($init.result.serverInfo | ConvertTo-Json -Compress))
        [void]$md.AppendLine("protocolVersion: " + $init.result.protocolVersion)
        [void]$md.AppendLine("capabilities: " + ($init.result.capabilities | ConvertTo-Json -Compress -Depth 10))
    }
    [void]$md.AppendLine(''); [void]$md.AppendLine('## Tool names'); [void]$md.AppendLine(((@($all | ForEach-Object { $_.name }) | Sort-Object) -join ', '))
    foreach ($tool in ($all | Sort-Object name)) {
        [void]$md.AppendLine(''); [void]$md.AppendLine("## $($tool.name)"); [void]$md.AppendLine('')
        [void]$md.AppendLine([string]$tool.description); [void]$md.AppendLine('')
        [void]$md.AppendLine($fence + 'json'); [void]$md.AppendLine((ConvertTo-Json -InputObject $tool.inputSchema -Depth 100)); [void]$md.AppendLine($fence)
        if ($tool.annotations) { [void]$md.AppendLine('annotations: ' + ($tool.annotations | ConvertTo-Json -Compress -Depth 10)) }
    }
    Save 'rea_tools.md' $md.ToString()
}

# ------------------------------------------------------------------------------------------------ STEP 3
Say ''
Say '=== STEP 3: OpenCode evidence ==='
$oc = $env:OPENCODE_COMMAND
if (-not $oc -and (Test-Path -LiteralPath (Join-Path $root '.env'))) {
    $mm = Select-String -LiteralPath (Join-Path $root '.env') -Pattern '^\s*OPENCODE_COMMAND\s*=\s*(.+?)\s*$' | Select-Object -First 1
    if ($mm) { $oc = $mm.Matches[0].Groups[1].Value.Trim([char]34, [char]39) }
}
if (-not $oc) { $oc = 'C:\Users\Palak\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe' }
Say "OpenCode executable: $oc (exists: $(Test-Path -LiteralPath $oc))"
$model = 'opencode/glm-5.3-flash-free'   # DEFAULT_MODEL in m2g\opencode_cli.py (opencode.json itself sets no model)
$info = New-Object Text.StringBuilder
$resVer = Invoke-Capture $oc '--version' 60
$resMcp = Invoke-Capture $oc 'mcp list' 120
$resMod = Invoke-Capture $oc 'models' 120
foreach ($item in @(@('opencode --version', $resVer), @('opencode mcp list', $resMcp), @('opencode models', $resMod))) {
    [void]$info.AppendLine("##### $($item[0])  [$(Describe-Result $item[1])]")
    [void]$info.AppendLine($item[1].Stdout); [void]$info.AppendLine('--- stderr ---'); [void]$info.AppendLine($item[1].Stderr); [void]$info.AppendLine('')
}
Save 'opencode_info.txt' $info.ToString()
Say ("OpenCode version: " + $resVer.Stdout.Trim() + "  [$(Describe-Result $resVer)]")
Say ("Model '$model' project default; listed by 'opencode models': " + [bool]($resMod.Stdout -match [regex]::Escape($model)) + "  [$(Describe-Result $resMod)]")
$reaLine = (($resMcp.Stdout + "`n" + $resMcp.Stderr) -split "`r?`n" | Where-Object { $_ -match 'rea' } | Select-Object -First 3) -join ' | '
Say ("'opencode mcp list' lines mentioning rea (connection status ONLY, not proof of tool use): " + $reaLine)

# 3c: one real run that asks the model to call exactly one REA tool, chosen from the real tools/list result.
$pick = $null
if ($all.Count -gt 0) {
    $names = @($all | ForEach-Object { $_.name })
    foreach ($n in @('binary_session', 'inspect_analysis_settings')) { if (-not $pick -and ($names -contains $n)) { $pick = $n } }
    if (-not $pick) {
        foreach ($tool in $all) {
            $req = @($tool.inputSchema.required)
            if ($req.Count -eq 0 -or ($req.Count -eq 1 -and -not $req[0])) { $pick = [string]$tool.name; break }
        }
    }
}
if ($SkipModelRun) { Say 'Model run skipped (-SkipModelRun). No evidence of REA tool use from OpenCode was collected.' }
elseif (-not $pick -or $pick -notmatch '^[A-Za-z0-9_\-]+$') { Say 'Model run skipped: no REA tool without required arguments was found in tools/list (step 2 incomplete).' }
else {
    $prompt = "Call the REA MCP tool named $pick (exposed to you as rea_$pick) exactly once with no arguments. Do not call any other tool and do not read files. Then reply with one short sentence saying whether the call succeeded or failed, including any error text."
    $permEnv = @{ OPENCODE_PERMISSION = '{"*":"deny","rea_*":"allow"}' }   # this run only; nothing is written to config
    Say "Running OpenCode once (tool requested: rea_$pick; waits until it exits or you cancel it)..."
    $run = Invoke-Capture $oc ('run ' + (Q $prompt) + ' --format json --model ' + $model) $null $permEnv
    Save 'opencode_run_events.jsonl' $run.Stdout
    Save 'opencode_run_stderr.txt' ($run.Stderr + "`r`n[" + (Describe-Result $run) + ']')
    Say ("OpenCode run: " + (Describe-Result $run))
    $evs = New-Object Collections.ArrayList; $nonJson = 0
    foreach ($ln in ($run.Stdout -split "`r?`n")) {
        if (-not $ln.Trim()) { continue }
        try { [void]$evs.Add(($ln | ConvertFrom-Json)) } catch { $nonJson++ }
    }
    $typeCounts = ($evs | Group-Object { [string]$_.type } | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ', '
    Say "Events parsed: $($evs.Count) (non-JSON lines: $nonJson); by type: $typeCounts"
    $toolEvs = @($evs | Where-Object { ([string]$_.type -match 'tool') -or ($_.part -and $_.part.type -eq 'tool') })
    $reaTools = New-Object Collections.ArrayList
    foreach ($e in $toolEvs) {
        $tn = [string]$e.part.tool; $st = [string]$e.part.state.status
        Say "  tool event: type=$($e.type) tool=$tn status=$st"
        if ($tn -match '^rea_') { [void]$reaTools.Add("$tn($st)") }
    }
    if ($reaTools.Count -eq 0 -and $run.Stdout -match '"tool"\s*:\s*"rea_') { [void]$reaTools.Add('(matched by raw text search; check opencode_run_events.jsonl)') }
    if ($reaTools.Count -gt 0) { Say ("REA TOOL CALL OBSERVED IN OPENCODE EVENTS: YES -> " + ($reaTools -join ', ')) }
    else { Say 'REA TOOL CALL OBSERVED IN OPENCODE EVENTS: NO (see opencode_run_events.jsonl; the raw file is authoritative)' }
    foreach ($e in @($evs | Where-Object { $_.type -eq 'error' })) { Say ("  error event: " + (Redact ($e | ConvertTo-Json -Compress -Depth 6))) }
}
$logDir = Join-Path $env:USERPROFILE '.local\share\opencode\log'
if (Test-Path -LiteralPath $logDir) {
    $lf = Get-ChildItem -LiteralPath $logDir -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($lf) { Save 'opencode_log_tail.txt' ((Get-Content -LiteralPath $lf.FullName -Tail 300) -join "`r`n"); Say "OpenCode log tail saved from $($lf.Name)" }
} else { Say "OpenCode default log folder not found at $logDir (no log tail saved)" }

# ------------------------------------------------------------------------------------------------ STEP 4
Say ''
Say '=== STEP 4: tests (pytest; run_tests.bat uses unittest discovery and would not collect the pytest-style new tests) ==='
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }
$testEnv = @{ PYTHONPATH = "$root;$root\tests" }
$r1 = Invoke-Capture $py '-m pytest tests/test_call_arguments.py' 600 $testEnv
Save 'pytest_call_arguments.txt' ($r1.Stdout + "`r`n" + $r1.Stderr + "`r`n[" + (Describe-Result $r1) + ']')
$tail1 = (($r1.Stdout -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1))
Say "pytest tests/test_call_arguments.py -> $(Describe-Result $r1) | $tail1"
$r2 = Invoke-Capture $py '-m pytest' 1800 $testEnv
Save 'pytest_full.txt' ($r2.Stdout + "`r`n" + $r2.Stderr + "`r`n[" + (Describe-Result $r2) + ']')
$tail2 = (($r2.Stdout -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1))
Say "pytest (full suite)                  -> $(Describe-Result $r2) | $tail2"

Say ''
Say "Done. Files are in: $diag"
