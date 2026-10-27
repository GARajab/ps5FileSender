# PS5 File Sender (Windows)

A small Windows desktop app for uploading files to a PS5 FTP server at the fixed remote directory `/data/homebrew`.

## Requirements on the PS5
- An FTP server/homebrew app must already be running on the PS5.
- Know the PS5's LAN IP address and FTP port.
- The server must accept anonymous FTP login (username `anonymous`, password `anonymous@`) and permit access/write to `/data/homebrew`.
- The remote directory must already exist. This app intentionally does not create folders automatically because server support and permissions vary.

## Build on Windows 10/11

1. Install Python 3.11 or 3.12 (64-bit) from https://www.python.org/downloads/windows/
   - During installation, enable **Add python.exe to PATH**.
2. Extract this project folder.
3. Open Command Prompt in the extracted folder.
4. Run:

   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install PySide6 pyinstaller
   python -m PyInstaller --noconfirm --clean --windowed --onefile --name PS5FileSender --collect-all PySide6 main.py
   ```

5. The executable will be at:

   `dist\PS5FileSender.exe`

Copy that EXE to any Windows 10/11 PC. Python is not needed on the receiving/user PC.

## Use

1. Run `PS5FileSender.exe`.
2. Enter the PS5 IP address and FTP port.
3. Click **Test Connection**. If successful, the fixed destination folder is reachable.
4. Add files with **Choose Files…** or drag files into the table.
5. Click **Send to PS5**.

The host and port are saved to `%APPDATA%\PS5FileSender\settings.json`.

## Notes / limitations
- This app uses plain FTP, not SFTP or FTPS. Standard FTP is not encrypted; use only on a trusted local network.
- It tries anonymous login with a non-empty email-style password. Some FTP servers require a blank password or a different login behavior; if the test fails with a login error, the server's authentication settings must be checked and the code adjusted to match.
- It uses passive FTP mode, which is commonly used by homebrew FTP servers. If the PS5 server uses active mode or has unusual passive-port/network behavior, transfer may fail.
- FTP uploads use `STOR` and can overwrite an existing file with the same name in `/data/homebrew`.
- Retry starts the file upload again from the beginning. It does not resume partial uploads.
- The app does not bypass PS5 firmware restrictions or FTP server permissions.
- Some PS5 FTP servers expose a different filesystem root. If `cwd('/data/homebrew')` fails, verify the path as seen by the FTP server; do not assume the host OS path and FTP-visible path are identical.
