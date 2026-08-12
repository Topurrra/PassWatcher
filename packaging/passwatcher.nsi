Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "LogicLib.nsh"
!include "WinMessages.nsh"

!ifndef VERSION
  !error "VERSION must be supplied with /DVERSION=<passwatcher version>"
!endif

!define PRODUCT_NAME "Passwatcher"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Passwatcher"
!define BUILD_DIR "${__FILEDIR__}\..\dist\passwatcher"
!define PATH_HELPER "${__FILEDIR__}\update_user_path.ps1"

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "${__FILEDIR__}\..\dist\Passwatcher-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\Passwatcher"
ShowInstDetails show
ShowUninstDetails show

Function BroadcastEnvironmentChange
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd

; PowerShell/.NET registry APIs avoid NSIS's fixed-size string registers, so long
; user PATH values are never truncated while exact case-insensitive segments are updated.
Function AddToUserPath
  InitPluginsDir
  File /oname=$PLUGINSDIR\update_user_path.ps1 "${PATH_HELPER}"
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\update_user_path.ps1" -Operation Add -Entry "$INSTDIR"'
  Pop $0
  StrCmp $0 "0" add_path_success
  DetailPrint "Unable to update the current-user PATH (exit code $0)."
  Abort
add_path_success:
  Call BroadcastEnvironmentChange
FunctionEnd

Function un.RemoveFromUserPath
  InitPluginsDir
  File /oname=$PLUGINSDIR\update_user_path.ps1 "${PATH_HELPER}"
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\update_user_path.ps1" -Operation Remove -Entry "$INSTDIR"'
  Pop $0
  StrCmp $0 "0" remove_path_success
  DetailPrint "Unable to remove Passwatcher from the current-user PATH (exit code $0)."
  Abort
remove_path_success:
  Call un.BroadcastEnvironmentChange
FunctionEnd

Function un.BroadcastEnvironmentChange
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd

Section "Passwatcher" SEC_PASSWATCHER
  SetShellVarContext current
  SetOverwrite on

  ; The installation directory contains application-owned runtime files only.
  ; Removing it first prevents stale PyInstaller files from surviving upgrades.
  RMDir /r "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${BUILD_DIR}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Passwatcher"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\pw.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$"$INSTDIR\uninstall.exe$"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$"$INSTDIR\uninstall.exe$" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1

  Call AddToUserPath

  ; Upgrades intentionally preserve $APPDATA\Passwatcher\config.toml.
  IfFileExists "$APPDATA\Passwatcher\config.toml" 0 +2
    DetailPrint "Preserved existing Passwatcher connection settings."
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Call un.RemoveFromUserPath
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  RMDir /r "$INSTDIR"

  MessageBox MB_YESNO|MB_ICONQUESTION "Remove local Passwatcher connection settings too?" /SD IDNO IDNO keep_config
    RMDir /r "$APPDATA\Passwatcher"
keep_config:
SectionEnd
