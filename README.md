# Storage Cleaner (str-cl)

## str-cl (Storage Cleaner) is a safe, cross-platform Command-line Tool made to enable you finding the large files on both your computer and on Android devices (via adb) and optionally clean them.
If your devices (laptop or Andriod phone) is filled up in the storage, you will be able to find what are the large files that fill up your storage, and you will be able to delete them just by running a command.

## Features:
- Scan local filesystem for large files (recursive, fast with os.scandir).
- Report top-N largest files and export results as JSON.
- Clean local files safely by moving them to the OS trash (via send2trash) or permanently delete.
- Scan Android device shared storage via adb (USB or wireless debugging).
- Robust Android scanning: tries find, stat, ls -lR and per-directory iteration (handles many OEM differences).
- Save last phone scan to ~/.str_cl_last_scan.json for deletion by index.
- Delete specific Android files by:
- explicit device path(s), or
- index from last scan (safe selection + confirmation).
- Dry-run and debug modes for safe testing.


### Prerequisites
- Python 3.8+
- Python packages (see requirements.txt or install below):
- click
- send2trash
- tqdm
- humanfriendly
- adb (Android Debug Bridge) for phone features.


## Installation

### ALL Operating Systems (Linux/Windows/macOS)
```
git clone https://github.com/InsaneHunterCTF/storage-cleaner.git
```
```
cd storage-cleaner
```
# Create a virtual environment and install Python deps (recommended)
```
python3 -m venv venv
```
```
source venv/bin/activate
```
```
pip install --upgrade pip
```
```
pip install -r requirements.txt
```
```
chmod +x str-cl.py
```
# For Ubuntu/Debian
```
sudo apt install android-tools-adb android-tools-fastboot
```
# For Fedora
```
sudo dnf install android-tools
```
# For Arch Linux
```
sudo pacman -S android-tools
```
# For macOS
```
brew install android-platform-tools
```
# For Windows
```
choco install adb
```
# usage
```
python3 str-cl.py --help
```

## Important Note:
In phone scanning, not always the files are listed in /sdcard, so it can returns nothing for you
try first:
```
adb shell find /sdcard -type f
```
if nothing was returned, then try:
```
adb shell ls /storage/emulated/0
```
if returned files, then in phone scanning, add the flag ``` --root /storage/emulated/0 ```
