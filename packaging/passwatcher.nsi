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

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "${__FILEDIR__}\..\dist\Passwatcher-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\Passwatcher"
ShowInstDetails show
ShowUninstDetails show

Function BroadcastEnvironmentChange
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd

; Add $INSTDIR only when it is not already a complete, case-insensitive PATH segment.
Function AddToUserPath
  ClearErrors
  ReadRegStr $0 HKCU "Environment" "Path"
  IfErrors add_path_empty
  StrCpy $1 0
  StrCpy $2 ""

add_path_scan:
  StrCpy $3 $0 1 $1
  StrCmp $3 "" add_path_last_segment
  StrCmp $3 ";" add_path_segment
  StrCpy $2 "$2$3"
  IntOp $1 $1 + 1
  Goto add_path_scan

add_path_segment:
  StrCmp $2 "$INSTDIR" add_path_done
  StrCpy $2 ""
  IntOp $1 $1 + 1
  Goto add_path_scan

add_path_last_segment:
  StrCmp $2 "$INSTDIR" add_path_done
  StrCmp $0 "" add_path_empty
  StrLen $1 $0
  IntOp $1 $1 - 1
  StrCpy $2 $0 1 $1
  StrCmp $2 ";" add_path_after_separator
  StrCpy $0 "$0;$INSTDIR"
  Goto add_path_write

add_path_after_separator:
  StrCpy $0 "$0$INSTDIR"
  Goto add_path_write

add_path_empty:
  StrCpy $0 "$INSTDIR"

add_path_write:
  WriteRegExpandStr HKCU "Environment" "Path" $0
  Call BroadcastEnvironmentChange

add_path_done:
FunctionEnd

; Rebuild PATH while removing every segment exactly equal to $INSTDIR.
; All other segments, including empty segments, retain their order.
Function un.RemoveFromUserPath
  ClearErrors
  ReadRegStr $0 HKCU "Environment" "Path"
  IfErrors remove_path_done
  StrCpy $1 0
  StrCpy $2 ""
  StrCpy $4 ""
  StrCpy $5 0

remove_path_scan:
  StrCpy $3 $0 1 $1
  StrCmp $3 "" remove_path_last_segment
  StrCmp $3 ";" remove_path_segment
  StrCpy $2 "$2$3"
  IntOp $1 $1 + 1
  Goto remove_path_scan

remove_path_segment:
  StrCmp $2 "$INSTDIR" remove_path_next
  StrCmp $5 1 0 remove_path_first
  StrCpy $4 "$4;"
remove_path_first:
  StrCpy $4 "$4$2"
  StrCpy $5 1
remove_path_next:
  StrCpy $2 ""
  IntOp $1 $1 + 1
  Goto remove_path_scan

remove_path_last_segment:
  StrCmp $2 "$INSTDIR" remove_path_write
  StrCmp $5 1 0 remove_path_last_first
  StrCpy $4 "$4;"
remove_path_last_first:
  StrCpy $4 "$4$2"
  StrCpy $5 1

remove_path_write:
  StrCmp $5 1 0 remove_path_delete_value
  WriteRegExpandStr HKCU "Environment" "Path" $4
  Goto remove_path_broadcast
remove_path_delete_value:
  DeleteRegValue HKCU "Environment" "Path"
remove_path_broadcast:
  Call un.BroadcastEnvironmentChange
remove_path_done:
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
