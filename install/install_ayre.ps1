# Installs Ayre on the gaming PC. Run from the unzipped ayre-wingman folder:
#   powershell -ExecutionPolicy Bypass -File install\install_ayre.ps1
# Needs: Wingman AI installed and started once, Python 3.11 (py launcher), Star Citizen.
# Safe to run again after any update or rebind: it rebuilds and recopies everything.
param(
    [string]$StarCitizen = "C:\Program Files\Roberts Space Industries\StarCitizen\LIVE"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$wingman = Join-Path $env:APPDATA "ShipBit\WingmanAI"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

Step "Checking what's installed"
if (-not (Test-Path $wingman)) { throw "Wingman AI not found. Install it from wingman-ai.com and start it once." }
$version = Get-ChildItem $wingman -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
    Sort-Object { [version]$_.Name } | Select-Object -Last 1
if (-not $version) { throw "No Wingman AI version folder yet. Start Wingman AI once, then rerun." }
$user = Join-Path $StarCitizen "user\client\0"
if (-not (Test-Path (Join-Path $user "Profiles\default\actionmaps.xml"))) { throw "Star Citizen bindings not found under $StarCitizen. Pass -StarCitizen <path to LIVE>." }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "Python not found. Install Python 3.11 from python.org (tick 'Add to PATH')." }
& py -3.11 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 not found. Install 3.11 from python.org (tick 'Add to PATH')." }
Write-Host "Wingman AI $($version.Name), Star Citizen at $StarCitizen"

Step "Building Ayre's keys from your current bindings"
$bindings = Join-Path $repo "bindings"
New-Item -ItemType Directory -Force $bindings | Out-Null
Copy-Item (Join-Path $user "Profiles\default\actionmaps.xml") $bindings -Force
Copy-Item (Join-Path $user "Profiles\default\attributes.xml") $bindings -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $user "controls\mappings\layout_*_exported.xml") $bindings -Force -Exclude "*_AYRE_*"
& py -3.11 -m pip install --quiet pyyaml
& py -3.11 (Join-Path $repo "sc_bindings\build_layer.py") $bindings
if ($LASTEXITCODE -ne 0) { throw "Building Ayre's keys failed (see above)." }
$ayreProfile = Get-ChildItem $bindings -Filter "layout_*_AYRE_exported.xml" | Select-Object -First 1
Copy-Item $ayreProfile.FullName (Join-Path $user "controls\mappings") -Force
Write-Host "Profile ready in game: $($ayreProfile.BaseName -replace '^layout_|_exported$','')"

Step "Installing Ayre's skills"
$custom = Join-Path $wingman "custom_skills"
foreach ($skill in "ayre_eyes", "ayre_flight", "ayre_ship") {
    $src = Join-Path $repo "skills\$skill"
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $custom $skill
    New-Item -ItemType Directory -Force $dst | Out-Null
    Copy-Item "$src\*" $dst -Recurse -Force
    $req = Join-Path $dst "requirements.txt"
    if (Test-Path $req) {
        & py -3.11 -m pip install --quiet --upgrade -r $req --target (Join-Path $dst "dependencies")
    }
    Write-Host "  $skill"
}

Step "Installing Ayre"
$configs = Join-Path $version.FullName "configs\_Star Citizen"
New-Item -ItemType Directory -Force $configs | Out-Null
$target = Join-Path $configs "Ayre.yaml"
if (Test-Path $target) { Copy-Item $target "$target.bak" -Force; Write-Host "  previous Ayre.yaml kept as Ayre.yaml.bak" }
Copy-Item (Join-Path $repo "templates\configs\_Star Citizen\Ayre.template.yaml") $target -Force
Copy-Item (Join-Path $repo "templates\configs\_Star Citizen\Ayre.png") $configs -Force

Step "Done"
Write-Host "1. Restart Wingman AI. Ayre appears under Star Citizen."
Write-Host "2. In Star Citizen: Options > Keybindings > Control Profiles > $($ayreProfile.BaseName -replace '^layout_|_exported$','')"
Write-Host "3. Set Star Citizen to borderless window so she can see the screen."
