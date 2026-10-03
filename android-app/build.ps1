$ErrorActionPreference='Stop'
$root=(Resolve-Path $PSScriptRoot).Path
$sdk=(Resolve-Path "$root\..\tools\android-sdk").Path
$bt="$sdk\build-tools\35.0.1"; $android="$sdk\platforms\android-35\android.jar"
$out="$root\build"; New-Item -ItemType Directory -Force "$out\res","$out\obj","$out\dex"|Out-Null
& "$bt\aapt2.exe" compile --dir "$root\res" -o "$out\res.zip"
if($LASTEXITCODE){exit $LASTEXITCODE}
& "$bt\aapt2.exe" link -o "$out\base.apk" -I $android --manifest "$root\AndroidManifest.xml" "$out\res.zip" --java "$out\gen" --min-sdk-version 26 --target-sdk-version 35
if($LASTEXITCODE){exit $LASTEXITCODE}
$sources=Get-ChildItem "$root\src","$out\gen" -Recurse -Filter *.java|ForEach-Object FullName
& javac -encoding UTF-8 -source 8 -target 8 -classpath $android -d "$out\obj" $sources
if($LASTEXITCODE){exit $LASTEXITCODE}
& "$bt\d8.bat" --lib $android --output "$out\dex" (Get-ChildItem "$out\obj" -Recurse -Filter *.class|ForEach-Object FullName)
if($LASTEXITCODE){exit $LASTEXITCODE}
Copy-Item "$out\base.apk" "$out\unsigned.apk" -Force
Push-Location "$out\dex";& "$bt\aapt.exe" add "$out\unsigned.apk" 'classes.dex';Pop-Location
if($LASTEXITCODE){exit $LASTEXITCODE}
& "$bt\zipalign.exe" -f 4 "$out\unsigned.apk" "$out\aligned.apk"
$ks="$root\debug.keystore";if(!(Test-Path $ks)){& keytool -genkeypair -keystore $ks -storepass android -alias androiddebugkey -keypass android -dname 'CN=Android Debug,O=Android,C=US' -keyalg RSA -validity 10000}
& "$bt\apksigner.bat" sign --ks $ks --ks-pass pass:android --key-pass pass:android --out "$root\..\outputs\SVIP-timer.apk" "$out\aligned.apk"
& "$bt\apksigner.bat" verify --verbose "$root\..\outputs\SVIP-timer.apk"
if($LASTEXITCODE){exit $LASTEXITCODE}
Copy-Item "$root\..\outputs\SVIP-timer.apk" "$root\..\outputs\SVIP-timer-v2.apk" -Force
