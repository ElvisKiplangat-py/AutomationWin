"""Randomized visible activity sessions for Cursor and VS Code on Kali/X11."""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Editor:
    name: str
    commands: tuple[str, ...]
    window_terms: tuple[str, ...]


EDITORS = (
    Editor("Cursor", ("cursor",), ("cursor",)),
    Editor("VS Code", ("code", "code-oss"), ("visual studio code", " - code", "vscode")),
)
MINIMUM_SIMULATED_KEY_DELAY_MS = 50


def is_wsl() -> bool:
    return "microsoft" in " ".join((os.environ.get("WSL_DISTRO_NAME", ""),
                                     Path("/proc/version").read_text(errors="ignore")
                                     if Path("/proc/version").exists() else "")).lower()


def is_windows() -> bool:
    return os.name == "nt"


def detached_process_options() -> dict[str, int | bool]:
    if is_windows():
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def windows_code_command(*arguments: str) -> list[str]:
    command = shutil.which("code") or shutil.which("code.cmd") or "code"
    if is_windows() and command.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command, *arguments]
    return [command, *arguments]


class WindowsInput:
    """Send Unicode keyboard input to the foreground Windows VS Code window."""

    POWERSHELL = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
function Invoke-ClipboardRetry([scriptblock]$Action) {
  $lastError=$null
  for($attempt=0; $attempt -lt 25; $attempt++) {
    try { return (& $Action) }
    catch { $lastError=$_.Exception; Start-Sleep -Milliseconds 80 }
  }
  throw "Windows clipboard stayed busy after retries: $($lastError.Message)"
}
function Get-ClipboardTextSafe {
  $hasText=Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::ContainsText() }
  if($hasText){ return Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetText() } }
  return $null
}
function Set-ClipboardTextSafe([string]$Text) {
  Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::SetText($Text,[System.Windows.Forms.TextDataFormat]::UnicodeText) }
}
$script:OriginalClipboard = Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetDataObject() }
$script:BridgeOwnsClipboard = $false
$script:LastBridgeText = $null
function Restore-BridgeClipboard {
  if($script:BridgeOwnsClipboard) {
    $stillOurs=(Get-ClipboardTextSafe) -eq $script:LastBridgeText
    if($stillOurs) {
      if($null -ne $script:OriginalClipboard){Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::SetDataObject($script:OriginalClipboard,$true) }} else {Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::Clear() }}
    } else {
      $script:OriginalClipboard=Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetDataObject() }
    }
    $script:BridgeOwnsClipboard=$false
  }
}
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WinInput {
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public UInt32 type; public U U; }
  [StructLayout(LayoutKind.Explicit, Size=32)] public struct U { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public UInt16 vk; public UInt16 scan; public UInt32 flags; public UInt32 time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public Int32 dx; public Int32 dy; public UInt32 data; public UInt32 flags; public UInt32 time; public IntPtr extra; }
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll", SetLastError=true)] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint n, INPUT[] i, int size);
  public static string ActiveTitle() { var h=GetForegroundWindow(); var s=new System.Text.StringBuilder(1024); GetWindowText(h,s,s.Capacity); uint p; GetWindowThreadProcessId(h,out p); try { return System.Diagnostics.Process.GetProcessById((int)p).ProcessName+"|"+s.ToString(); } catch { return "|"+s.ToString(); } }
  static void Check(uint sent) { if(sent!=1) throw new Exception("SendInput failed (Windows error "+Marshal.GetLastWin32Error()+")"); }
  public static void Key(ushort vk) { var a=new INPUT[1]; a[0].type=1; a[0].U.ki.vk=vk; Check(SendInput(1,a,Marshal.SizeOf(typeof(INPUT)))); a[0].U.ki.flags=2; Check(SendInput(1,a,Marshal.SizeOf(typeof(INPUT)))); }
  public static void Editor() { var a=new INPUT[4]; for(int n=0;n<4;n++) a[n].type=1; a[0].U.ki.vk=0x11; a[1].U.ki.vk=0x31; a[2].U.ki.vk=0x31; a[2].U.ki.flags=2; a[3].U.ki.vk=0x11; a[3].U.ki.flags=2; if(SendInput(4,a,Marshal.SizeOf(typeof(INPUT)))!=4) throw new Exception("SendInput failed (Windows error "+Marshal.GetLastWin32Error()+")"); }
  public static void Char(char c) { var a=new INPUT[1]; a[0].type=1; a[0].U.ki.scan=c; a[0].U.ki.flags=4; Check(SendInput(1,a,Marshal.SizeOf(typeof(INPUT)))); a[0].U.ki.flags=6; Check(SendInput(1,a,Marshal.SizeOf(typeof(INPUT)))); }
  public static void MoveRandomInCodeWindow() { var h=GetForegroundWindow(); var r=new RECT(); if(!GetWindowRect(h,out r)) throw new Exception("Could not read VS Code window bounds"); var x=new Random().Next(r.Left+12,Math.Max(r.Left+13,r.Right-12)); var y=new Random().Next(r.Top+50,Math.Max(r.Top+51,r.Bottom-12)); if(!SetCursorPos(x,y)) throw new Exception("Could not move pointer"); }
  public static void ClickActivityBar() { var h=GetForegroundWindow(); var r=new RECT(); if(!GetWindowRect(h,out r)) throw new Exception("Could not read VS Code window bounds"); var x=r.Left+24; var y=r.Top+100+(new Random().Next(0,6)*40); if(!SetCursorPos(x,y)) throw new Exception("Could not move pointer"); mouse_event(0x0002,0,0,0,UIntPtr.Zero); mouse_event(0x0004,0,0,0,UIntPtr.Zero); Editor(); }
}
"@
while ($line = [Console]::ReadLine()) {
  try {
    $q = $line | ConvertFrom-Json
    switch ($q.op) {
      'active' { $v=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([WinInput]::ActiveTitle())); [Console]::WriteLine("OK $v") }
      'move' { if([WinInput]::ActiveTitle().StartsWith('Code|',[StringComparison]::OrdinalIgnoreCase)){[WinInput]::MoveRandomInCodeWindow();[Console]::WriteLine('OK')}else{[Console]::WriteLine('PAUSED')} }
      'safe_click' { if([WinInput]::ActiveTitle().StartsWith('Code|',[StringComparison]::OrdinalIgnoreCase)){[WinInput]::ClickActivityBar();[Console]::WriteLine('OK')}else{[Console]::WriteLine('PAUSED')} }
      'type' {
        if(-not [WinInput]::ActiveTitle().StartsWith('Code|',[StringComparison]::OrdinalIgnoreCase)) {
          Restore-BridgeClipboard
          [Console]::WriteLine('PAUSED')
        } else {
          $s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($q.text))
          $elements=[Globalization.StringInfo]::GetTextElementEnumerator($s)
          if($elements.MoveNext()) {
            $piece=$elements.GetTextElement()
            if($script:BridgeOwnsClipboard) {
              $stillOurs=(Get-ClipboardTextSafe) -eq $script:LastBridgeText
              if(-not $stillOurs) {$script:OriginalClipboard=Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetDataObject() }; $script:BridgeOwnsClipboard=$false}
            }
            if(-not $script:BridgeOwnsClipboard) {$script:OriginalClipboard=Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetDataObject() }}
            if($piece -eq "`n") {$piece="`r`n"}
            Set-ClipboardTextSafe $piece
            $script:LastBridgeText=$piece
            $script:BridgeOwnsClipboard=$true
            if(-not [WinInput]::ActiveTitle().StartsWith('Code|',[StringComparison]::OrdinalIgnoreCase)) {Restore-BridgeClipboard; [Console]::WriteLine('PAUSED')}
            else {[System.Windows.Forms.SendKeys]::SendWait('^v'); if($q.delay -gt 0){Start-Sleep -Milliseconds ([int]$q.delay)}; [Console]::WriteLine('OK')}
          } else {[Console]::WriteLine('OK')}
        }
      }
      'key' {
        if($q.name -eq 'enter') {
          if(-not $script:BridgeOwnsClipboard) {$script:OriginalClipboard=Invoke-ClipboardRetry { [System.Windows.Forms.Clipboard]::GetDataObject() }}
          Set-ClipboardTextSafe "`r`n"
          $script:LastBridgeText="`r`n"
          $script:BridgeOwnsClipboard=$true
          [System.Windows.Forms.SendKeys]::SendWait('^v')
        } elseif($q.name -eq 'backspace') {[WinInput]::Key(0x08)} elseif($q.name -eq 'editor'){[WinInput]::Editor()} elseif($q.name -eq 'save'){[System.Windows.Forms.SendKeys]::SendWait('^s')}
        [Console]::WriteLine('OK')
      }
      'restore' {Restore-BridgeClipboard; [Console]::WriteLine('OK')}
    }
  } catch { [Console]::WriteLine('ERR '+$_.Exception.Message) }
}
'''

    def __init__(self) -> None:
        import base64
        command = shutil.which("powershell.exe") or shutil.which("powershell")
        if command is None:
            raise RuntimeError("Could not find powershell.exe. Enable WSL interop to send input to Windows VS Code.")
        self.process = subprocess.Popen(
            [command, "-NoLogo", "-NoProfile", "-NonInteractive", "-Sta", "-EncodedCommand",
             base64.b64encode(self.POWERSHELL.encode("utf-16le")).decode("ascii")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        self._request({"op": "active"})

    def _request(self, request: dict[str, object]) -> str:
        import json
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline().rstrip("\r\n")
        if response.startswith("ERR ") or not response:
            raise RuntimeError(response[4:] if response.startswith("ERR ") else "PowerShell input bridge stopped.")
        if response in ("OK", "PAUSED"):
            return response
        return response[3:] if response.startswith("OK ") else ""

    def active(self) -> str:
        import base64
        value = self._request({"op": "active"})
        return base64.b64decode(value).decode("utf-8", errors="replace")

    def require_focus(self) -> None:
        process, title = self.active().split("|", 1)
        if process.lower() != "code" or not title:
            raise RuntimeError("Typing stopped because Windows VS Code lost focus. No further source text will be sent.")

    def wait_until_focused(self) -> None:
        announced = False
        while True:
            process, title = self.active().split("|", 1)
            if process.lower() == "code" and title:
                if announced:
                    self.key("editor")
                    print("VS Code is active again; resuming.")
                return
            if not announced:
                print("Paused while another application is active. Return to VS Code to resume.")
                announced = True
            time.sleep(0.25)

    def wait_for_focus(self, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active().split("|", 1)[0].lower() == "code":
                self.key("editor")  # VS Code: focus the editor group (Ctrl+1).
                time.sleep(0.2)
                return
            time.sleep(0.25)
        raise RuntimeError("Focus the Windows VS Code window within 30 seconds; no source text was typed.")

    def wait_for_file_open(self, file_path: Path, timeout: float = 60) -> None:
        expected = file_path.name.casefold()
        deadline = time.monotonic() + timeout
        consecutive_matches = 0
        while time.monotonic() < deadline:
            process, title = self.active().split("|", 1)
            if process.casefold() == "code" and expected in title.casefold():
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    self.key("editor")
                    time.sleep(0.5)  # Let VS Code finish creating and laying out the new editor tab.
                    print(f"VS Code opened {file_path.name}; starting replay.")
                    return
            else:
                consecutive_matches = 0
            time.sleep(0.25)
        raise RuntimeError(
            f"VS Code did not show {file_path.name} as the active file within {timeout:.0f} seconds. "
            "No source text was typed; bring the new file into view and retry."
        )

    def type_text(self, text: str, delay_ms: int) -> None:
        import base64
        for character in text:
            request = {"op": "type", "text": base64.b64encode(character.encode("utf-8")).decode("ascii"), "delay": delay_ms}
            while True:
                self.wait_until_focused()
                if self._request(request) == "OK":
                    break

    def key(self, name: str) -> None:
        self._request({"op": "key", "name": name})

    def move_pointer(self) -> None:
        self.wait_until_focused()
        self._request({"op": "move"})

    def safe_click(self) -> None:
        self.wait_until_focused()
        self._request({"op": "safe_click"})

    def close(self) -> None:
        if self.process.stdin:
            try:
                self._request({"op": "restore"})
            except (OSError, RuntimeError):
                pass
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()

def create_output_file(workspace: str, source: Path, index: int) -> Path:
    """Create an unused destination path without touching any existing file."""
    folder = Path(workspace) / "replayed_code"
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{index:03}_{source.name}"
    candidate = folder / base
    suffix = 1
    while candidate.exists():
        candidate = folder / f"{index:03}_{source.stem}_{suffix}{source.suffix}"
        suffix += 1
    candidate.touch(exist_ok=False)
    return candidate


def require_tools() -> None:
    if is_windows():
        if shutil.which("code") is None and shutil.which("code.cmd") is None:
            raise RuntimeError("Could not find the VS Code command. Install VS Code and enable its command on PATH.")
        return
    if is_wsl():
        if shutil.which("powershell.exe") is None and shutil.which("powershell") is None:
            raise RuntimeError("Could not find powershell.exe. Enable WSL interop to control Windows VS Code.")
        if shutil.which("code") is None:
            raise RuntimeError("Could not find the VS Code WSL command. Open this folder with VS Code's 'Reopen Folder in WSL' command.")
        return
    missing = [name for name in ("wmctrl", "xdotool") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "Missing system tools: " + ", ".join(missing)
            + ". Install with: sudo apt update && sudo apt install wmctrl xdotool"
        )
    if not ("DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ):
        raise RuntimeError(
            "No graphical display detected. On WSL, enable WSLg or connect to "
            "an X server, then run this from a terminal with DISPLAY configured."
        )


def list_windows() -> list[tuple[str, str]]:
    result = subprocess.run(
        ["wmctrl", "-l", "-x"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "wmctrl could not list desktop windows")
    windows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 4)
        if len(fields) >= 5:
            windows.append((fields[0], fields[4]))
    return windows


def window_for(editor: Editor) -> tuple[str, str] | None:
    for window_id, title in list_windows():
        if any(term in title.lower() for term in editor.window_terms):
            return window_id, title
    return None


def window_geometry(window_id: str) -> tuple[int, int, int, int]:
    result = subprocess.run(
        ["xdotool", "getwindowgeometry", "--shell", window_id],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Could not read geometry for window {window_id}")
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    try:
        return tuple(int(values[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Unexpected geometry from xdotool: {result.stdout}") from exc


def active_window_id() -> str | None:
    result = subprocess.run(
        ["xdotool", "getactivewindow"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return f"0x{int(result.stdout.strip()):x}"
    except ValueError:
        return result.stdout.strip().lower()


def require_editor_focus(window_id: str) -> None:
    active = active_window_id()
    try:
        active_number = int(active, 16) if active is not None else None
        target_number = int(window_id, 16)
    except ValueError:
        active_number = None
        target_number = -1
    if active_number is None or active_number != target_number:
        raise RuntimeError(
            "Typing stopped because the selected editor window lost focus. "
            "No further source text will be sent."
        )


def wait_for_editor_focus(window_id: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = active_window_id()
        try:
            if active is not None and int(active, 16) == int(window_id, 16):
                return
        except ValueError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Could not confirm the selected editor has focus. No source text was typed.")


def launch_or_activate(editor: Editor, workspace: str, file_to_open: Path | None = None) -> str:
    if is_wsl() or is_windows():
        if editor.name != "VS Code":
            raise RuntimeError("The Windows input bridge currently supports VS Code only; use --editor \"VS Code\".")
        # Open/activate the workspace first. The destination file is opened in
        # a separate, awaited step so typing cannot land in the previous tab.
        subprocess.Popen(windows_code_command("--reuse-window", workspace),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached_process_options())
        print("Activated the VS Code workspace; waiting for the destination file before typing.")
        return "windows-vscode"
    window = window_for(editor)
    if window is None:
        executable = next((shutil.which(command) for command in editor.commands if shutil.which(command)), None)
        if executable is None:
            raise RuntimeError(
                f"Could not find {editor.name}. Install it or ensure its launcher is on PATH."
            )
        subprocess.Popen(
            [executable, str(file_to_open or Path(workspace))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached_process_options(),
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            time.sleep(0.5)
            window = window_for(editor)
            if window:
                break
    if window is None:
        raise RuntimeError(f"{editor.name} launched, but its window did not appear within 45 seconds.")
    window_id, title = window
    subprocess.run(["wmctrl", "-ia", window_id], check=False)
    wait_for_editor_focus(window_id)
    print(f"[{time.strftime('%H:%M:%S')}] Activated {editor.name}: {title}")
    return window_id


def open_file_in_editor(
    editor: Editor, file_path: Path, windows_input: WindowsInput | None = None,
) -> str:
    if is_wsl() or is_windows():
        subprocess.Popen(windows_code_command("--reuse-window", str(file_path)),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached_process_options())
        if windows_input:
            windows_input.wait_for_file_open(file_path)
        return "windows-vscode"
    executable = next((shutil.which(command) for command in editor.commands if shutil.which(command)), None)
    if executable is None:
        raise RuntimeError(f"Could not find {editor.name}. Install it or ensure its launcher is on PATH.")
    subprocess.Popen(
        [executable, "--reuse-window", str(file_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_process_options(),
    )
    deadline = time.monotonic() + 20
    window = None
    while time.monotonic() < deadline:
        window = window_for(editor)
        if window:
            break
        time.sleep(0.25)
    if not window:
        raise RuntimeError(f"Could not find {editor.name}'s window after opening {file_path.name}.")
    subprocess.run(["wmctrl", "-ia", window[0]], check=False)
    wait_for_editor_focus(window[0])
    return window[0]


def replay_source(
    source: Path, destination: Path, window_id: str, min_key_delay_ms: int,
    max_key_delay_ms: int, min_line_pause: float,
    max_line_pause: float, thinking_pause_chance: float,
    typo_chance: float, slowdown_factor: float,
    target_hours_per_10000_lines: float,
    windows_input: WindowsInput | None = None,
    click_enabled: bool = False,
) -> None:
    """Type source text into the focused destination using xdotool keystrokes."""
    content = source.read_text(encoding="utf-8")
    if not content:
        print(f"Skipping empty source: {source}")
        return

    lines = content.splitlines(keepends=True)
    average_speed_factor = (1 + slowdown_factor) / 2
    target_seconds = target_hours_per_10000_lines * 3600 * len(lines) / 10000
    nominal_seconds = len(content) * ((min_key_delay_ms + max_key_delay_ms) / 2) / 1000
    nominal_seconds += len(lines) * (
        (min_line_pause + max_line_pause) / 2
        + thinking_pause_chance * 2.25
    )
    nominal_seconds += len(content) * typo_chance * 0.9
    nominal_seconds *= average_speed_factor
    pace_scale = target_seconds / nominal_seconds if nominal_seconds else 1.0
    estimated_seconds = target_seconds
    print(
        f"Typing {len(lines):,} lines ({len(content):,} characters) from {source} "
        f"into {destination}; estimated time {estimated_seconds / 60:.1f} minutes "
        "before occasional thinking pauses."
    )
    for line_number, line in enumerate(lines):
        if windows_input:
            windows_input.wait_until_focused()
        else:
            require_editor_focus(window_id)
        # xdotool types literal text as simulated key events. Per-line calls keep
        # indentation and punctuation intact and allow a human-like line rhythm.
        text = line.rstrip("\r\n")
        progress = line_number / max(1, len(lines) - 1)
        pace = 1 + (slowdown_factor - 1) * progress
        key_delay = max(
            MINIMUM_SIMULATED_KEY_DELAY_MS,
            round(random.randint(min_key_delay_ms, max_key_delay_ms) * pace * pace_scale),
        )
        if text:
            # Occasionally type one wrong character, pause, then correct it.
            # This leaves the destination identical to the source.
            typo_positions = [
                pos for pos, char in enumerate(text)
                if char.isalnum() and random.random() < typo_chance
            ]
            cursor = 0
            for typo_position in typo_positions:
                if typo_position < cursor:
                    continue
                if typo_position > cursor:
                    if windows_input:
                        windows_input.type_text(text[cursor:typo_position], key_delay)
                    else:
                        subprocess.run(["xdotool", "type", "--window", window_id, "--clearmodifiers", "--delay", str(key_delay), "--", text[cursor:typo_position]], check=True)
                correct = text[typo_position]
                wrong = random.choice("abcdefghijklmnopqrstuvwxyz")
                while wrong.lower() == correct.lower():
                    wrong = random.choice("abcdefghijklmnopqrstuvwxyz")
                if windows_input:
                    windows_input.type_text(wrong, key_delay)
                else:
                    subprocess.run(["xdotool", "type", "--window", window_id, "--clearmodifiers", "--delay", str(key_delay), "--", wrong], check=True)
                time.sleep(random.uniform(0.12, 0.45) * pace * pace_scale)
                if windows_input:
                    windows_input.wait_until_focused()
                    windows_input.key("backspace")
                else:
                    require_editor_focus(window_id)
                    subprocess.run(["xdotool", "key", "--window", window_id, "--clearmodifiers", "BackSpace"], check=True)
                time.sleep(random.uniform(0.08, 0.3) * pace * pace_scale)
                if windows_input:
                    windows_input.wait_until_focused()
                    windows_input.type_text(correct, key_delay)
                else:
                    require_editor_focus(window_id)
                    subprocess.run(["xdotool", "type", "--window", window_id, "--clearmodifiers", "--delay", str(key_delay), "--", correct], check=True)
                cursor = typo_position + 1
            if cursor < len(text):
                if windows_input:
                    windows_input.type_text(text[cursor:], key_delay)
                else:
                    subprocess.run(["xdotool", "type", "--window", window_id, "--clearmodifiers", "--delay", str(key_delay), "--", text[cursor:]], check=True)
        if line.endswith(("\n", "\r")):
            if windows_input:
                windows_input.wait_until_focused()
                windows_input.key("enter")
            else:
                require_editor_focus(window_id)
                subprocess.run(["xdotool", "key", "--window", window_id, "--clearmodifiers", "Return"], check=True)
            pause = random.uniform(min_line_pause, max_line_pause) * pace * pace_scale
            if not text.strip():
                pause += random.uniform(0.4, 1.2) * pace_scale
            if random.random() < thinking_pause_chance:
                pause += random.uniform(1.0, 3.5) * pace_scale
            # Click only the Activity Bar, then restore editor focus. Never
            # click inside code or the integrated terminal.
            if click_enabled and random.random() < 0.035:
                if windows_input:
                    windows_input.safe_click()
                else:
                    x, y, _, height = window_geometry(window_id)
                    click_x = x + 24
                    click_y = y + random.randint(100, max(101, height - 80))
                    subprocess.run(
                        ["xdotool", "mousemove", "--sync", str(click_x), str(click_y), "click", "1"],
                        stdout=subprocess.DEVNULL, check=False,
                    )
                    subprocess.run(
                        ["xdotool", "key", "--window", window_id, "ctrl+1"],
                        stdout=subprocess.DEVNULL, check=False,
                    )
            # Pointer movement is harmless without a click.
            if random.random() < 0.18:
                if windows_input:
                    windows_input.move_pointer()
                else:
                    x, y = random_point(window_id)
                    subprocess.run(
                        ["xdotool", "mousemove", "--sync", str(x), str(y)],
                        stdout=subprocess.DEVNULL, check=False,
                    )
            time.sleep(pause)
    print(f"Finished typing {len(lines)} lines from {source}")
    if windows_input:
        windows_input.wait_until_focused()
        windows_input.key("save")
        time.sleep(1)
        saved = destination.read_text(encoding="utf-8")
        if saved != content:
            mismatch = next(
                (index for index, (expected, actual) in enumerate(zip(content, saved))
                 if expected != actual),
                min(len(content), len(saved)),
            )
            line = content.count("\n", 0, mismatch) + 1
            raise RuntimeError(
                f"Saved output differs from source at line {line}; refusing to report a successful replay. "
                "Check that VS Code finished saving and that no extension changed the text."
            )
        print("Verified saved output matches the source exactly.")


def random_point(window_id: str, margin: int = 36) -> tuple[int, int]:
    x, y, width, height = window_geometry(window_id)
    left, top = x + margin, y + margin
    right, bottom = x + width - margin, y + height - margin
    if right <= left or bottom <= top:
        raise RuntimeError("Editor window is too small for safe pointer movement.")
    return random.randint(left, right), random.randint(top, bottom)


def activity_session(
    editor: Editor, workspace: str, duration: float, click: bool,
    source: Path | None, output_index: int,
    min_key_delay_ms: int, max_key_delay_ms: int,
    min_line_pause: float, max_line_pause: float,
    thinking_pause_chance: float, typo_chance: float, slowdown_factor: float,
    target_hours_per_10000_lines: float,
    windows_input: WindowsInput | None = None,
) -> None:
    destination = create_output_file(workspace, source, output_index) if source else None
    window_id = launch_or_activate(editor, workspace, destination)
    if source and destination:
        window_id = open_file_in_editor(editor, destination, windows_input)
        replay_source(
            source, destination, window_id, min_key_delay_ms, max_key_delay_ms,
            min_line_pause, max_line_pause, thinking_pause_chance,
            typo_chance, slowdown_factor, target_hours_per_10000_lines, windows_input,
            click,
        )
        # Code replay is confined to the selected editor and exits immediately
        # after the source is typed. Do not move/click the pointer afterward.
        return
    end_at = time.monotonic() + duration
    print(f"{editor.name} session: about {duration / 60:.1f} min")
    while time.monotonic() < end_at:
        if windows_input:
            windows_input.wait_until_focused()
            windows_input.move_pointer()
            if click and random.random() < 0.12:
                windows_input.safe_click()
            time.sleep(random.uniform(15, 55))
            continue
        x, y = random_point(window_id)
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(x), str(y)],
            stdout=subprocess.DEVNULL, check=False,
        )
        if click and random.random() < 0.12:
            # The activity bar is a narrow icon rail; do not click editor or
            # terminal content. Restore the editor group after changing a view.
            x, y, _, height = window_geometry(window_id)
            click_x = x + 24
            click_y = y + random.randint(100, max(101, height - 80))
            subprocess.run(
                ["xdotool", "mousemove", "--sync", str(click_x), str(click_y), "click", "1"],
                stdout=subprocess.DEVNULL, check=False,
            )
            subprocess.run(
                ["xdotool", "key", "--window", window_id, "ctrl+1"],
                stdout=subprocess.DEVNULL, check=False,
            )
        time.sleep(random.uniform(15, 55))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Folder to open in each editor (defaults to current folder).")
    parser.add_argument("--editor", choices=("VS Code", "Cursor"), default="VS Code", help="Editor to type into (default: VS Code).")
    parser.add_argument("--min-session", type=int, default=8, help="Shortest editor session in minutes (default: 8).")
    parser.add_argument("--max-session", type=int, default=24, help="Longest editor session in minutes (default: 24).")
    parser.add_argument("--min-break", type=int, default=1, help="Shortest pause between sessions in minutes (default: 1).")
    parser.add_argument("--max-break", type=int, default=5, help="Longest pause between sessions in minutes (default: 5).")
    parser.add_argument("--once", action="store_true", help="Run one session in each editor, then exit.")
    parser.add_argument("--click", action="store_true", help="Enable occasional clicks on VS Code's Activity Bar, then restore editor focus.")
    parser.add_argument("--source", action="append", type=Path, default=[], help="Source code file to replay. Repeat this option to set file order.")
    parser.add_argument("--min-key-delay-ms", type=int, default=250, help="Minimum delay between simulated key events in milliseconds (default: 250).")
    parser.add_argument("--max-key-delay-ms", type=int, default=500, help="Maximum delay between simulated key events in milliseconds (default: 500).")
    parser.add_argument("--min-line-pause", type=float, default=0.8, help="Minimum pause after each line in seconds (default: 0.8).")
    parser.add_argument("--max-line-pause", type=float, default=1.8, help="Maximum pause after each line in seconds (default: 1.8).")
    parser.add_argument("--thinking-pause-chance", type=float, default=0.04, help="Chance of an extra 1–3.5 second pause after a line (default: 0.04).")
    parser.add_argument("--typo-chance", type=float, default=0.002, help="Chance per alphanumeric character of a temporary typo corrected with Backspace (default: 0.002).")
    parser.add_argument("--slowdown-factor", type=float, default=2.5, help="Multiply typing delays progressively up to this factor by the end of a file (default: 2.5).")
    parser.add_argument("--target-hours-per-10000-lines", type=float, default=30.0, help="Approximate total typing time for 10,000 source lines (default: 30 hours).")
    parser.add_argument("--seed", type=int, help="Optional random seed for repeatable schedules.")
    args = parser.parse_args()
    if args.min_session <= 0 or args.max_session < args.min_session:
        parser.error("session bounds must be positive and max-session >= min-session")
    if args.min_break < 0 or args.max_break < args.min_break:
        parser.error("break bounds must be nonnegative and max-break >= min-break")
    if (args.min_key_delay_ms < 0 or args.max_key_delay_ms < args.min_key_delay_ms
            or args.min_line_pause < 0 or args.max_line_pause < args.min_line_pause
            or not 0 <= args.thinking_pause_chance <= 1
            or not 0 <= args.typo_chance <= 1 or args.slowdown_factor < 1
            or args.target_hours_per_10000_lines <= 0):
        parser.error("typing delays must be nonnegative and ordered; probabilities must be between 0 and 1; slowdown-factor must be at least 1")
    for source in args.source:
        if not source.is_file():
            parser.error(f"source file does not exist: {source}")
    return args


def main() -> int:
    if not (sys.platform.startswith("linux") or is_windows()):
        print("This version targets Windows and Kali/Linux desktop sessions.", file=sys.stderr)
        return 2
    args = parse_args()
    if is_wsl() and not args.source:
        print("Error: WSL mode currently supports source replay only. Add --source FILE (and usually --once).", file=sys.stderr)
        return 2
    try:
        require_tools()
        windows_input = WindowsInput() if (is_wsl() or is_windows()) else None
        if not windows_input:
            import pyautogui
    except (RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            print("Install Python dependencies with: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    except Exception as exc:
        if is_windows():
            print("Error: Could not start the Windows input bridge. Run this from an interactive desktop session with Windows PowerShell available.", file=sys.stderr)
            print(f"Details: {exc}", file=sys.stderr)
            return 2
        # pyautogui connects to X while importing (through mouseinfo), so a
        # stale DISPLAY or missing Xauthority file otherwise produces a long
        # traceback before the script can start.
        print(
            "Error: Could not connect to the X display. In WSL, this script "
            "needs WSLg (or another X server) and a Linux GUI editor window; "
            "Windows VS Code windows are not controlled by wmctrl/xdotool. "
            "Check DISPLAY and XAUTHORITY for the active display.",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)
    workspace = str(Path(args.workspace or Path.cwd()).resolve())
    if not windows_input:
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
    try:
        while True:
            selected_editor = next(editor for editor in EDITORS if editor.name == args.editor)
            run_order = [(source, selected_editor) for source in args.source]
            if not args.source:
                run_order = [(None, selected_editor)]
            for output_index, (source, editor) in enumerate(run_order, start=1):
                duration = random.uniform(args.min_session, args.max_session) * 60
                activity_session(
                    editor, workspace, duration, args.click,
                    source, output_index,
                    args.min_key_delay_ms, args.max_key_delay_ms,
                    args.min_line_pause, args.max_line_pause,
                    args.thinking_pause_chance, args.typo_chance,
                    args.slowdown_factor, args.target_hours_per_10000_lines,
                    windows_input,
                )
                if not args.once:
                    pause = random.uniform(args.min_break, args.max_break) * 60
                    print(f"Pausing for about {pause / 60:.1f} min")
                    time.sleep(pause)
            if args.once:
                break
    except KeyboardInterrupt:
        if windows_input:
            windows_input.close()
        print("\nStopped by user.")
    except Exception as exc:
        if windows_input:
            windows_input.close()
        if not windows_input:
            try:
                import pyautogui
                if isinstance(exc, pyautogui.FailSafeException):
                    print("\nStopped by mouse fail-safe (move pointer to the top-left corner).")
                    return 0
            except ImportError:
                pass
        if isinstance(exc, (RuntimeError, OSError)):
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        raise
    if windows_input:
        windows_input.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
