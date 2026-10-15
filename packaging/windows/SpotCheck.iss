#ifndef AppVersion
  #error AppVersion is required
#endif

[Setup]
AppId={{8E6F2B43-5C1D-4E7A-9B3F-2D6C1A7E9F10}
AppName=SpotCheck
AppVersion={#AppVersion}
AppVerName=SpotCheck {#AppVersion}
AppPublisher=djkvido
AppPublisherURL=https://github.com/djkvido/spotcheck
AppSupportURL=https://github.com/djkvido/spotcheck/issues
AppUpdatesURL=https://github.com/djkvido/spotcheck/releases
AppCopyright=Copyright (C) 2026 djkvido
DefaultDirName={localappdata}\Programs\SpotCheck
DefaultGroupName=SpotCheck
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=SpotCheck-{#AppVersion}-windows-x64-setup
SetupIconFile={#RepoDir}\spotcheck\static\spotcheck.ico
UninstallDisplayIcon={app}\runtime\SpotCheck.exe
UninstallDisplayName=SpotCheck
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
WizardImageFile={#RepoDir}\packaging\windows\wizard.png
WizardSmallImageFile={#RepoDir}\packaging\windows\wizard-small.png
WizardImageFileDynamicDark={#RepoDir}\packaging\windows\wizard.png
WizardSmallImageFileDynamicDark={#RepoDir}\packaging\windows\wizard-small.png
ShowLanguageDialog=auto
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=SpotCheck
VersionInfoDescription=SpotCheck installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "{#SourceDir}\TERMS.txt"
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"; LicenseFile: "{#SourceDir}\TERMS.cs.txt"

[Messages]
english.WizardLicense=Terms of Use
english.LicenseLabel3=Please read the following Terms of Use. You must accept them before continuing with the installation.
english.LicenseAccepted=I &accept the Terms of Use
english.LicenseNotAccepted=I &do not accept the Terms of Use
czech.WizardLicense=Podmínky použití
czech.LicenseLabel3=Přečtěte si prosím následující podmínky použití. Před pokračováním v instalaci s nimi musíte souhlasit.
czech.LicenseAccepted=&Souhlasím s podmínkami použití
czech.LicenseNotAccepted=&Nesouhlasím s podmínkami použití

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
Source: "{#SourceDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\TERMS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\TERMS.cs.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SpotCheck"; Filename: "{app}\runtime\SpotCheck.exe"; Parameters: "-m spotcheck --packaged"; WorkingDir: "{app}"
Name: "{autodesktop}\SpotCheck"; Filename: "{app}\runtime\SpotCheck.exe"; Parameters: "-m spotcheck --packaged"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\SpotCheck.exe"; Parameters: "-m spotcheck --packaged"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,SpotCheck}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\runtime\python.exe"; Parameters: "-m spotcheck --quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitSpotCheck"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\runtime\python.exe')) then
    Exec(ExpandConstant('{app}\runtime\python.exe'), '-m spotcheck --quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
