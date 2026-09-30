; ServiceBills on-prem installer (Inno Setup 6.3+).
; Build:  ISCC.exe /DAppVersion=1.2.3 /DGhcrUser=<user> /DGhcrToken=<read-only token> ServiceBills.iss
; See README.md. The GHCR token is compiled into the setup binary (read-only pull token) and only
; passed to install.ps1 as an argument; the installer never writes it to disk itself.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef GhcrUser
  #define GhcrUser ""
#endif
#ifndef GhcrToken
  #define GhcrToken ""
#endif

[Setup]
AppId={{7D3B6C0A-5E1F-4C52-9A0B-2F6E8B1D4C93}
AppName=ServiceBills
AppVersion={#AppVersion}
AppPublisher=ServiceBills
DefaultDirName={commonappdata}\ServiceBills
DisableDirPage=yes
DefaultGroupName=ServiceBills
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputBaseFilename=ServiceBills-Setup
WizardStyle=modern
UninstallDisplayName=ServiceBills
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\docker-compose.yml"; DestDir: "{app}\setup-src"; Flags: ignoreversion
Source: "..\env.template"; DestDir: "{app}\setup-src"; Flags: ignoreversion
Source: "..\scripts\*"; DestDir: "{app}\setup-src\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{commondesktop}\ServiceBills"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" start.ps1"; WorkingDir: "{app}"
Name: "{group}\ServiceBills"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" start.ps1"; WorkingDir: "{app}"
Name: "{group}\Backup now"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" backup.ps1"; WorkingDir: "{app}"
Name: "{group}\Restore backup"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" restore.ps1"; WorkingDir: "{app}"
Name: "{group}\Check for updates"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" update.ps1"; WorkingDir: "{app}"
Name: "{group}\Stop ServiceBills"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" stop.ps1"; WorkingDir: "{app}"
Name: "{group}\Uninstall ServiceBills"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\run-hidden.vbs"" start.ps1"; Description: "Open ServiceBills now"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded

[Code]
const
  DockerUrl = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe';
  DockerTermsUrl = 'https://www.docker.com/legal/docker-subscription-service-agreement/';
  MinDiskBytes = 21474836480;   { 20 GB }
  MinRamBytes = 7516192768;     { 7 GB: an 8 GB PC reports slightly less than 8 GB usable }

type
  TMemoryStatusEx = record
    dwLength: Cardinal;
    dwMemoryLoad: Cardinal;
    ullTotalPhys: Int64;
    ullAvailPhys: Int64;
    ullTotalPageFile: Int64;
    ullAvailPageFile: Int64;
    ullTotalVirtual: Int64;
    ullAvailVirtual: Int64;
    ullAvailExtendedVirtual: Int64;
  end;

function GlobalMemoryStatusEx(var lpBuffer: TMemoryStatusEx): Boolean;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';
function PostMessage(hWnd: HWND; Msg: UINT; wParam: LongInt; lParam: LongInt): Boolean;
  external 'PostMessageW@user32.dll stdcall';

var
  DockerPage: TWizardPage;
  DockerAccept: TNewCheckBox;
  OptionsPage: TInputDirWizardPage;
  AutoUpdateCheck: TNewCheckBox;
  DownloadPage: TDownloadWizardPage;
  InstallOk: Boolean;
  InstallError: String;
  SilentCancel: Boolean;

function IsResume: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RESUME') = 0 then
      Result := True;
end;

function DockerInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\Docker\Docker\Docker Desktop.exe'));
end;

function InstallSucceeded: Boolean;
begin
  Result := InstallOk;
end;

function InitializeSetup: Boolean;
var
  Mem: TMemoryStatusEx;
  FreeB, TotalB: Int64;
  Drive: String;
  ResultCode: Integer;
begin
  Result := True;

  { 1. RAM: warn only. }
  Mem.dwLength := SizeOf(Mem);
  if GlobalMemoryStatusEx(Mem) then
    if Mem.ullTotalPhys < MinRamBytes then
      if MsgBox('This PC has less than 8 GB of memory. ServiceBills may run slowly.' + #13#10#13#10 +
                'Do you want to continue anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
      begin
        Result := False;
        Exit;
      end;

  { 2. Free disk space on the system drive: block. }
  Drive := ExtractFileDrive(ExpandConstant('{sys}')) + '\';
  if GetSpaceOnDisk64(Drive, FreeB, TotalB) then
    if FreeB < MinDiskBytes then
    begin
      MsgBox('ServiceBills needs at least 20 GB of free disk space on drive ' + Drive + '.' + #13#10 +
             'Please free up some space and run this Setup again.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

  { 3. Virtualization: exit 0 = enabled, 1 = disabled, anything else = could not tell (do not block). }
  if Exec('powershell.exe',
          '-NoProfile -Command "try { if (((Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled -contains $true) -or (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) { exit 0 } else { exit 1 } } catch { exit 2 }"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    if ResultCode = 1 then
    begin
      MsgBox('Virtualization is turned off on this PC, and ServiceBills needs it.' + #13#10#13#10 +
             'Restart the PC, open BIOS/UEFI setup (usually F2, F10, Del or Esc at startup), ' +
             'enable "Intel VT-x" / "AMD-V" / "SVM" / "Virtualization Technology", ' +
             'save and restart, then run this Setup again.', mbError, MB_OK);
      Result := False;
    end;
end;

procedure DockerLinkClick(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', DockerTermsUrl, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function DefaultBackupDir: String;
var
  Candidate: String;
begin
  Candidate := GetEnv('OneDrive');
  if (Candidate = '') or (not DirExists(Candidate)) then
    Candidate := ExtractFilePath(RemoveBackslash(ExpandConstant('{userdocs}'))) + 'OneDrive';
  if DirExists(Candidate) then
    Result := Candidate + '\ServiceBills Backups'
  else if DirExists('G:\My Drive') then
    Result := 'G:\My Drive\ServiceBills Backups'
  else
    Result := ExpandConstant('{userdocs}') + '\ServiceBills Backups';
end;

procedure InitializeWizard;
var
  Info, Link: TNewStaticText;
begin
  { Docker page }
  DockerPage := CreateCustomPage(wpWelcome, 'Docker Desktop',
    'ServiceBills runs inside Docker Desktop, which is not installed on this PC yet.');

  Info := TNewStaticText.Create(DockerPage);
  Info.Parent := DockerPage.Surface;
  Info.WordWrap := True;
  Info.AutoSize := False;
  Info.Left := 0;
  Info.Top := 0;
  Info.Width := DockerPage.SurfaceWidth;
  Info.Height := ScaleY(90);
  Info.Caption := 'Docker Desktop is a program that runs ServiceBills safely in the background. ' +
    'Setup will download it (several hundred MB) and install it for you. Docker Desktop is licensed by ' +
    'Docker Inc.; larger companies may need a paid Docker subscription.';

  Link := TNewStaticText.Create(DockerPage);
  Link.Parent := DockerPage.Surface;
  Link.AutoSize := True;
  Link.Left := 0;
  Link.Top := Info.Top + Info.Height + ScaleY(4);
  Link.Caption := 'Read Docker''s license terms';
  Link.Font.Color := clBlue;
  Link.Font.Style := [fsUnderline];
  Link.Cursor := crHand;
  Link.OnClick := @DockerLinkClick;

  DockerAccept := TNewCheckBox.Create(DockerPage);
  DockerAccept.Parent := DockerPage.Surface;
  DockerAccept.Left := 0;
  DockerAccept.Top := Link.Top + ScaleY(32);
  DockerAccept.Width := DockerPage.SurfaceWidth;
  DockerAccept.Caption := 'I accept Docker''s terms';
  DockerAccept.Checked := False;

  { Options page }
  OptionsPage := CreateInputDirPage(DockerPage.ID, 'Backups and updates',
    'Choose where ServiceBills keeps its daily backups.',
    'Pick a folder that is synced to OneDrive or Google Drive if you can, so backups survive a PC failure.',
    False, '');
  OptionsPage.Add('Backup folder:');
  OptionsPage.Values[0] := DefaultBackupDir;

  AutoUpdateCheck := TNewCheckBox.Create(OptionsPage);
  AutoUpdateCheck.Parent := OptionsPage.Surface;
  AutoUpdateCheck.Left := 0;
  AutoUpdateCheck.Top := ScaleY(110);
  AutoUpdateCheck.Width := OptionsPage.SurfaceWidth;
  AutoUpdateCheck.Caption := 'Keep ServiceBills up to date automatically (recommended)';
  AutoUpdateCheck.Checked := True;

  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = DockerPage.ID then
    Result := DockerInstalled or IsResume;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if SilentCancel then
    Confirm := False;
end;

function InstallDocker: Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  DownloadPage.Clear;
  DownloadPage.Add(DockerUrl, 'DockerDesktopInstaller.exe', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      DownloadPage.SetText('Installing Docker Desktop...', 'This can take several minutes. Please wait.');
      if not Exec(ExpandConstant('{tmp}\DockerDesktopInstaller.exe'),
                  'install --quiet --accept-license --backend=wsl-2', '', SW_HIDE,
                  ewWaitUntilTerminated, ResultCode) then
        MsgBox('Could not start the Docker Desktop installer.', mbError, MB_OK)
      else if ResultCode = 3010 then
      begin
        RegWriteStringValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
          'ServiceBillsSetup', '"' + ExpandConstant('{srcexe}') + '" /RESUME');
        MsgBox('Docker Desktop was installed, but Windows needs to restart to finish.' + #13#10#13#10 +
               'Please restart this PC now. Setup will continue automatically when you sign in again.',
               mbInformation, MB_OK);
        { Close the wizard; CancelButtonClick suppresses the "Exit Setup?" prompt. }
        SilentCancel := True;
        PostMessage(WizardForm.Handle, $0010, 0, 0);
      end
      else if ResultCode <> 0 then
        MsgBox('The Docker Desktop installer failed (code ' + IntToStr(ResultCode) + ').' + #13#10 +
               'Please check your internet connection and try again.', mbError, MB_OK)
      else
        Result := True;
    except
      MsgBox('Could not download Docker Desktop:' + #13#10 + GetExceptionMessage + #13#10#13#10 +
             'Please check your internet connection and try again.', mbError, MB_OK);
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DockerPage.ID then
  begin
    if not DockerAccept.Checked then
    begin
      MsgBox('Please tick "I accept Docker''s terms" to continue.', mbInformation, MB_OK);
      Result := False;
    end
    else
      Result := InstallDocker;
  end
  else if CurPageID = OptionsPage.ID then
  begin
    if Trim(OptionsPage.Values[0]) = '' then
    begin
      MsgBox('Please choose a backup folder.', mbInformation, MB_OK);
      Result := False;
    end;
  end;
end;

{ Quote a value as a single-quoted PowerShell string. }
function PsQuote(const S: String): String;
var
  T: String;
begin
  T := S;
  StringChangeEx(T, '''', '''''', True);
  Result := '''' + T + '''';
end;

function FirstLine(const S: String): String;
var
  P: Integer;
begin
  Result := Trim(S);
  P := Pos(#10, Result);
  if P > 0 then
    Result := Trim(Copy(Result, 1, P - 1));
end;

procedure RunInstallScript;
var
  ErrFile, AutoArg, PsCmd, CmdLine, Line: String;
  ResultCode: Integer;
  ErrText: AnsiString;
begin
  ErrFile := ExpandConstant('{tmp}\servicebills-install-err.txt');
  DeleteFile(ErrFile);
  if AutoUpdateCheck.Checked then
    AutoArg := '$true'
  else
    AutoArg := '$false';

  PsCmd := '& ' + PsQuote(ExpandConstant('{app}\setup-src\scripts\install.ps1')) +
    ' -SourceDir ' + PsQuote(ExpandConstant('{app}\setup-src')) +
    ' -BackupDir ' + PsQuote(RemoveBackslash(Trim(OptionsPage.Values[0]))) +
    ' -AutoUpdate:' + AutoArg +
    ' -GhcrUser ' + PsQuote('{#GhcrUser}') +
    ' -GhcrToken ' + PsQuote('{#GhcrToken}') +
    ' -ImageTag ' + PsQuote('{#AppVersion}') +
    '; exit $LASTEXITCODE';

  { cmd /S /C strips the outermost quotes, which lets us redirect stderr to a file. }
  CmdLine := '/S /C ""' + ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe') +
    '" -NoProfile -ExecutionPolicy Bypass -Command "' + PsCmd + '" 2>"' + ErrFile + '""';

  WizardForm.StatusLabel.Caption := 'Starting ServiceBills - this can take several minutes the first time...';
  WizardForm.FilenameLabel.Caption := '';
  if not Exec(ExpandConstant('{cmd}'), CmdLine, ExpandConstant('{app}'), SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
  begin
    InstallOk := False;
    InstallError := 'Could not start the installation script.';
  end
  else if ResultCode = 0 then
    InstallOk := True
  else
  begin
    InstallOk := False;
    InstallError := 'The installation script failed (code ' + IntToStr(ResultCode) + ').';
    if LoadStringFromFile(ErrFile, ErrText) then
    begin
      Line := FirstLine(String(ErrText));
      if Line <> '' then
        InstallError := Line;
    end;
  end;

  if not InstallOk then
    if MsgBox('ServiceBills could not be started:' + #13#10#13#10 + InstallError + #13#10#13#10 +
              'Open the log folder to see details?', mbError, MB_YESNO) = IDYES then
      Exec('explorer.exe', ExpandConstant('{commonappdata}\ServiceBills\logs'), '', SW_SHOWNORMAL,
           ewNoWait, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunInstallScript;
end;

function ReadAppBaseUrl: String;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(ExpandConstant('{commonappdata}\ServiceBills\.env'), Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      if Pos('APP_BASE_URL=', Lines[I]) = 1 then
        Result := Trim(Copy(Lines[I], Length('APP_BASE_URL=') + 1, Length(Lines[I])));
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Url: String;
begin
  if CurPageID = wpFinished then
  begin
    if InstallOk then
    begin
      Url := ReadAppBaseUrl;
      if Url <> '' then
        WizardForm.FinishedLabel.Caption := 'ServiceBills is installed and running.' + #13#10#13#10 +
          'Open it from any device on your network at:' + #13#10 + Url
      else
        WizardForm.FinishedLabel.Caption := 'ServiceBills is installed and running.';
    end
    else
      WizardForm.FinishedLabel.Caption := 'ServiceBills was installed but could not be started:' + #13#10 +
        InstallError + #13#10#13#10 + 'Logs: ' + ExpandConstant('{commonappdata}\ServiceBills\logs');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DeleteData: Boolean;
  Args: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    DeleteData := False;
    if not UninstallSilent then
      if MsgBox('Also delete all ServiceBills data (customers, payments, backups)? This cannot be undone.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        if MsgBox('Are you sure? All customers, payments and backups will be permanently deleted.',
                  mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
          DeleteData := True;

    Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\uninstall.ps1') + '"';
    if DeleteData then
      Args := Args + ' -DeleteData';
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Args, ExpandConstant('{app}'),
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
