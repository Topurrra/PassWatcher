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
!define BUILD_DIR "..\dist\passwatcher"
!define PATH_HELPER "update_user_path.ps1"
!define INSTALL_DIR "$LOCALAPPDATA\Programs\Passwatcher"

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "..\dist\Passwatcher-Setup-${VERSION}.exe"
InstallDir "${INSTALL_DIR}"
ShowInstDetails show
ShowUninstDetails show

; Runtime /D= and _?= switches may initialize $INSTDIR to an arbitrary path.
; Ignore those overrides and require the one per-user target before any mutation.
Function .onInit
  SetShellVarContext current
  StrCmp $INSTDIR "${INSTALL_DIR}" installer_target_ready
  StrCpy $INSTDIR "${INSTALL_DIR}"
  StrCmp $INSTDIR "${INSTALL_DIR}" installer_target_ready
  MessageBox MB_OK|MB_ICONSTOP "Passwatcher could not validate its per-user install directory."
  Abort
installer_target_ready:
FunctionEnd

Function un.onInit
  SetShellVarContext current
  StrCmp $INSTDIR "${INSTALL_DIR}" uninstaller_target_ready
  StrCpy $INSTDIR "${INSTALL_DIR}"
  StrCmp $INSTDIR "${INSTALL_DIR}" uninstaller_target_ready
  MessageBox MB_OK|MB_ICONSTOP "Passwatcher could not validate its per-user install directory."
  Abort
uninstaller_target_ready:
FunctionEnd

Function BroadcastEnvironmentChange
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd

; PowerShell/.NET registry APIs avoid NSIS's fixed-size string registers, so long
; user PATH values are never truncated while exact case-insensitive segments are updated.
Function AddToUserPath
  InitPluginsDir
  File /oname=$PLUGINSDIR\update_user_path.ps1 "${PATH_HELPER}"
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\update_user_path.ps1" -Operation Add -Entry "$INSTDIR"'
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
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\update_user_path.ps1" -Operation Remove -Entry "$INSTDIR"'
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

  ; Replace only Passwatcher-owned artifacts. Unexpected files keep the root
  ; directory non-empty and therefore survive both upgrades and uninstall.
  Delete "$INSTDIR\pw.exe"
  Delete "$INSTDIR\passwatcher.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  RMDir "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${BUILD_DIR}\*.*"
  File /oname=passwatcher.exe "${BUILD_DIR}\pw.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Passwatcher"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\pw.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
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
  ; /ifempty considers subkeys but not values, so enumerate values first to
  ; preserve unrelated current-user state that Passwatcher does not own.
  ClearErrors
  EnumRegValue $0 HKCU "Software\Passwatcher\Installer" 0
  IfErrors installer_state_has_no_values
  Goto installer_state_cleanup_done
installer_state_has_no_values:
  DeleteRegKey /ifempty HKCU "Software\Passwatcher\Installer\"
installer_state_cleanup_done:
  ClearErrors
  EnumRegValue $0 HKCU "Software\Passwatcher" 0
  IfErrors product_state_has_no_values
  Goto product_state_cleanup_done
product_state_has_no_values:
  DeleteRegKey /ifempty HKCU "Software\Passwatcher\"
product_state_cleanup_done:
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  SetOutPath "$TEMP"
  Delete "$INSTDIR\pw.exe"
  Delete "$INSTDIR\passwatcher.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  RMDir "$INSTDIR"

  MessageBox MB_YESNO|MB_ICONQUESTION "Remove local Passwatcher connection settings too?" /SD IDNO IDNO keep_config
    Delete "$APPDATA\Passwatcher\config.toml"
    RMDir "$APPDATA\Passwatcher"
keep_config:
SectionEnd
