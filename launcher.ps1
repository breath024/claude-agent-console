# Claude Code 런처 (주제별 병렬 탭)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projects = [ordered]@{
  '1' = @{ name = '홈';            path = 'C:\Users\USER' }
  '2' = @{ name = '내 프로젝트';   path = 'C:\Users\USER\Desktop\myproject' }
  '3' = @{ name = '주식봇';        path = 'C:\Users\USER\Desktop\창업\주식봇' }
  '4' = @{ name = '코인봇';        path = 'C:\Users\USER\Desktop\창업\코인봇' }
  '5' = @{ name = '중국어 공부';   path = 'C:\Users\USER\Desktop\중국어 공부' }
  '6' = @{ name = '창업 폴더 전체'; path = 'C:\Users\USER\Desktop\창업' }
}

# 실제 목록은 projects.json(개인 경로라 저장소에 안 올라감)에서 읽는다.
# 파일이 없으면 위 기본값 그대로 동작한다. 콘솔 앱(agent_console.py)과 같은 파일을 쓴다.
$pf = Join-Path $PSScriptRoot 'projects.json'
if (Test-Path $pf) {
  try {
    $loaded = Get-Content $pf -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($loaded) {
      $projects = [ordered]@{}
      $i = 1
      foreach ($p in $loaded) {
        $projects["$i"] = @{ name = $p.name; path = $p.path }
        $i++
      }
    }
  } catch { }
}

while ($true) {
  Clear-Host
  Write-Host ''
  Write-Host '   ===========================================' -ForegroundColor Cyan
  Write-Host '            CLAUDE CODE  런처'               -ForegroundColor Cyan
  Write-Host '   ===========================================' -ForegroundColor Cyan
  Write-Host '     주제마다 새 탭(창)으로 병렬 실행됩니다.'  -ForegroundColor DarkGray
  Write-Host '   ===========================================' -ForegroundColor Cyan
  Write-Host ''
  foreach ($k in $projects.Keys) {
    Write-Host ("     [{0}]  {1}" -f $k, $projects[$k].name)
  }
  Write-Host ''
  Write-Host '     [0]  직접 경로 입력'
  Write-Host '     [Q]  런처 종료'                          -ForegroundColor DarkGray
  Write-Host '   -------------------------------------------'
  Write-Host ''

  $sel = Read-Host '  프로젝트 번호'

  if ($sel -eq 'q' -or $sel -eq 'Q') { break }

  $proj = $null
  if ($sel -eq '0') {
    $proj = Read-Host '  폴더 경로 붙여넣기'
  } elseif ($projects.Contains($sel)) {
    $proj = $projects[$sel].path
  }

  if ([string]::IsNullOrWhiteSpace($proj)) {
    Write-Host '   잘못 입력했어. 아무 키나 누르면 다시.' -ForegroundColor Yellow
    [void][System.Console]::ReadKey($true)
    continue
  }
  if (-not (Test-Path -LiteralPath $proj)) {
    Write-Host ("   그 폴더가 없어:  {0}" -f $proj) -ForegroundColor Red
    [void][System.Console]::ReadKey($true)
    continue
  }

  $topic = Read-Host '  이번 창 주제 (탭 이름)'
  if ([string]::IsNullOrWhiteSpace($topic)) { $topic = 'Claude' }

  # 같은 ClaudeCode 윈도우에 새 탭으로 띄움 (위쪽 탭, 병렬)
  $inner = 'title ' + $topic + ' & claude'
  & wt -w ClaudeCode new-tab --title $topic --suppressApplicationTitle -d $proj cmd /k $inner

  Clear-Host
  Write-Host ''
  Write-Host '   ===========================================' -ForegroundColor Green
  Write-Host ("     새 탭 열림  >  [{0}]" -f $topic)         -ForegroundColor Green
  Write-Host ("     위치: {0}" -f $proj)                     -ForegroundColor DarkGray
  Write-Host '   ===========================================' -ForegroundColor Green
  Write-Host ''
  Write-Host '     - 위쪽 탭에서 방금 만든 창 확인'
  Write-Host '     - Ctrl+Tab 으로 탭 이동'
  Write-Host '     - 여기서 계속 새 주제 창을 만들 수 있어'
  Write-Host ''
  $again = Read-Host '  [Enter] 또 만들기 / [Q] 종료'
  if ($again -eq 'q' -or $again -eq 'Q') { break }
}
