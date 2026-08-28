; Privacy Filter - Local : self-extracting launcher (NOT a traditional install)
; ---------------------------------------------------------------------------
; Produces a single .exe that, when run, extracts the portable app into a
; "PrivacyFilter" subfolder NEXT TO the .exe itself ($EXEDIR\PrivacyFilter) and
; launches it. No admin rights, no registry entries, no Program Files. To
; uninstall, the user just deletes that subfolder.
;
; The runtime folders (model/ ner-models/ cache/ tmp/ logs/) are intentionally
; NOT packed, so re-running the .exe refreshes the app without wiping the
; already-downloaded models (~2.7 GB for the PII model, ~1.2 GB for the name
; models). python/, app/ and tesseract/ are the shipped payload and ARE
; replaced.
;
; Build (from the repo root, after build_portable.ps1 produced portable-build\):
;   makensis /DVERSION=2.4.0 /DSRCDIR="<abs>\portable-build" \
;            /DOUTFILE="<abs>\dist\PrivacyFilter-Setup-v2.4.0.exe" \
;            packaging\windows\installer.nsi
; Or use the helper:  packaging\windows\make_exe.ps1

Unicode true

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef SRCDIR
  !define SRCDIR "..\..\portable-build"
!endif
!ifndef OUTFILE
  !define OUTFILE "PrivacyFilter-Setup-v${VERSION}.exe"
!endif

Name "Privacy Filter - Local"
OutFile "${OUTFILE}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "Privacy Filter - Local v${VERSION}"

; Extract into a subfolder next to the .exe. $EXEDIR is the directory the
; installer is run from (resolved from the exe path, not the cwd), so this works
; even when launched from the Downloads folder.
InstallDir "$EXEDIR\PrivacyFilter"

ShowInstDetails show
AutoCloseWindow true

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Privacy Filter - Local"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "Privacy Filter - Local (portable, self-extracting)"
VIAddVersionKey "LegalCopyright" "Apache-2.0"

Page instfiles

Section "Privacy Filter"
  SetOutPath "$INSTDIR"

  ; Remove the previous payload so stale files from an older version don't
  ; linger. model/ ner-models/ cache/ tmp/ logs/ live elsewhere under $INSTDIR
  ; and are preserved, so an update never re-downloads the ~4 GB of models.
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\tesseract"

  File /r "${SRCDIR}\python"
  File /r "${SRCDIR}\app"
  ; Guarded because build_portable.ps1 warns and carries on when it cannot get
  ; Tesseract, rather than failing the whole build. Such a package still works
  ; on documents with a text layer, and smoke_ocr.py fails the release before
  ; one reaches anyone.
!if /FileExists "${SRCDIR}\tesseract"
  File /r "${SRCDIR}\tesseract"
!else
  !warning "No tesseract/ in ${SRCDIR}: this installer has no OCR for scanned PDFs."
!endif
  File "${SRCDIR}\launch.bat"
  File "${SRCDIR}\start.bat"
  File "${SRCDIR}\uninstall.bat"
  File "${SRCDIR}\Privacy Filter.vbs"

  DetailPrint "Starting Privacy Filter..."
  ; Launch the hidden launcher via the .vbs; the installer window then closes.
  Exec '"$SYSDIR\wscript.exe" "$INSTDIR\Privacy Filter.vbs"'
SectionEnd
