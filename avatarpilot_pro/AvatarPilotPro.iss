; ============================================================================
; AvatarPilot Pro — Inno Setup Script
;
; Gera o instalador Windows (AvatarPilotPro-Setup-X.Y.Z.exe).
; Empacota apenas o CORE (~150MB): server.py, license_system, frontend, scripts,
; launcher e setup. O venv311 e os modelos sao baixados na primeira execucao.
;
; Como compilar:
;   1) Instale Inno Setup 6 (https://jrsoftware.org/isinfo.php)
;   2) Abra este arquivo no Inno Setup Compiler e clique em Build
;      OU pela linha de comando:
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AvatarPilotPro.iss
; ============================================================================

#define AppName        "AvatarPilot Pro"
#define AppVersion     "1.0.0"
#define AppPublisher   "Guilherme Chaves"
#define AppExeName     "Start AvatarPilot Pro.bat"
#define AppURL         "https://github.com/guichaves13-cmd/gcg-automation"

[Setup]
AppId={{F1E9B7A3-9C5E-4A2D-8F1B-C7A4E5D2F8B6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=AvatarPilotPro-Setup-{#AppVersion}
SetupIconFile=
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\Start AvatarPilot Pro.bat

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
; CORE — necessario sempre (~150MB)
Source: "server.py";                    DestDir: "{app}"; Flags: ignoreversion
Source: "license_system.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "face_swap_worker.py";          DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist
Source: "gfpgan_worker.py";             DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist
Source: "requirements.txt";             DestDir: "{app}"; Flags: ignoreversion
Source: "Start AvatarPilot Pro.bat";    DestDir: "{app}"; Flags: ignoreversion
Source: "first_run_setup.bat";          DestDir: "{app}"; Flags: ignoreversion
Source: "RESUMO_AVATARPILOT.md";        DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist
Source: "LICENSE.txt";                  DestDir: "{app}"; Flags: ignoreversion
; Frontend (HTML/JS/CSS)
Source: "templates\*";                  DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "static\*";                     DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
; Scripts auxiliares (downloads de modelos)
Source: "scripts\*";                    DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Backgrounds opcionais
Source: "backgrounds\*";                DestDir: "{app}\backgrounds"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
; Cria pastas que o app precisa (vazias na instalacao)
Name: "{app}\uploads"
Name: "{app}\outputs"
Name: "{app}\data"
Name: "{app}\logs"
Name: "{app}\venv311"
Name: "{app}\models"

[Icons]
Name: "{group}\AvatarPilot Pro"; Filename: "{app}\Start AvatarPilot Pro.bat"; WorkingDir: "{app}"
Name: "{group}\Desinstalar AvatarPilot Pro"; Filename: "{uninstallexe}"
Name: "{userdesktop}\AvatarPilot Pro"; Filename: "{app}\Start AvatarPilot Pro.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Start AvatarPilot Pro.bat"; Description: "Iniciar AvatarPilot Pro agora"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Limpa caches/logs ao desinstalar — preserva uploads/outputs/licenca do usuario
Type: filesandordirs; Name: "{app}\logs"
Type: files;          Name: "{app}\*.pyc"
Type: filesandordirs; Name: "{app}\__pycache__"
