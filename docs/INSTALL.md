# Installing Personal Tutor on Windows

This guide takes you from nothing to talking with your tutor. You don't need to know how to program: the whole installation is **one double-click**, and this page explains what happens and what to do if something goes wrong.

[العربية](INSTALL.ar.md) · [Back to README](../README.md)

---

## 1. Check your computer

| | You need | How to check |
|---|---|---|
| **Windows** | Windows 10 or 11, 64-bit | *Settings → System → About → System type* |
| **Graphics card** | **NVIDIA** with at least **12 GB** of video memory (16 GB recommended) | *Task Manager → Performance → GPU*: the name at the top right and **Dedicated GPU memory** |
| **NVIDIA driver** | A recent one (2025 or newer) | Update with the **NVIDIA App** or from [nvidia.com/drivers](https://www.nvidia.com/Download/index.aspx) |
| **Disk space** | **25 GB** free (about **40 GB** with the Arabic voice) | *File Explorer → This PC* |
| **Internet** | Only for the first installation (20–30 GB of downloads) | |
| **Browser** | Chrome or Microsoft Edge (Edge is built into Windows) | |

> **Why NVIDIA only?** The language model, speech recognition, both voices and the pronunciation model all run on the graphics card with CUDA. That's what makes it fast and private. There's deliberately no slow CPU mode.

**12 GB vs 16 GB:** with 12–15 GB everything works, but skip the optional Arabic voice. With 16 GB you can install everything (measured: 11.7–13.2 GB in use).

---

## 2. Download the project

**Option A, no Git (easiest):**

1. On the [project's GitHub page](https://github.com/abdalluh17777/personal-local-ai-tutor), click the green **Code** button, then **Download ZIP**.
2. Right-click the ZIP, choose **Extract All…**, and pick a drive with enough free space, for example `D:\`.
3. You now have a folder such as `D:\personal-tutor`. You can rename or move it before installing.

**Option B, with Git:**

```powershell
cd D:\
git clone https://github.com/abdalluh17777/personal-local-ai-tutor.git personal-tutor
```

> 💡 Choose a short path on a drive with space. Avoid putting the folder inside OneDrive or another synced folder, since gigabytes of models would be uploaded.

---

## 3. Run the installer

Open the folder and **double-click `Install Personal Tutor.cmd`**.

If Windows shows **"Windows protected your PC"**, click **More info → Run anyway**. Windows shows this for any downloaded script. The installer only writes inside the project folder and needs no administrator rights.

A window opens and works through 9 steps:

| Step | What it does | Download |
|---|---|---|
| 1. Checking this computer | Finds your NVIDIA card, video memory, driver and free space, and asks whether to add the Arabic voice | — |
| 2. uv | The Python package manager (portable) | 18 MB |
| 3. Ollama | Runs the language model on your GPU (portable, so no Windows service or tray icon) | 1.4 GB |
| 4. eSpeak NG | Phonemes for the English voice and the pronunciation check | 13 MB |
| 5. Python environment | Python 3.12, FastAPI, faster-whisper, ONNX Runtime GPU and the CUDA 12 runtime | ~3 GB |
| 6. Speech models | Kokoro (English voice), Whisper large-v3-turbo (your speech to text), and the pronunciation model, built from its original weights in 5–10 minutes | ~4 GB |
| 7. Language model | Qwen 3.5 9B, set up as the `tutor` model with every layer on the GPU | 6.6 GB |
| 8. Arabic voice *(optional)* | SILMA TTS in its own environment, so Arabic words in answers are spoken | ~11 GB |
| 9. Start and load courses | Starts the tutor, loads the 6 bundled courses and the word lists, and offers a desktop shortcut | — |

It usually takes **30–60 minutes**, depending on your internet speed. When you see **"Done. Personal Tutor is installed and running."**, press a key and the app opens.

> **Interrupted?** Just double-click the installer again. Finished steps are skipped and downloads resume where they stopped.

### Installer options (advanced)

From PowerShell in the project folder:

```powershell
.\scripts\setup.ps1 -WithArabicVoice   # add the Arabic voice without asking (also adds it to an existing install)
.\scripts\setup.ps1 -NoArabicVoice     # skip it without asking
.\scripts\setup.ps1 -NoShortcut        # no desktop shortcut
.\scripts\setup.ps1 -Yes               # accept every default (unattended install)
```

---

## 4. Start, use and stop

| | |
|---|---|
| ▶️ **Start** | Double-click **`Start Personal Tutor.cmd`** or the **Personal Tutor** desktop shortcut. The app opens in its own window. The first answer takes a few seconds while the model loads onto the GPU. |
| ⏹️ **Stop** | Double-click **`Stop Personal Tutor.cmd`**. It stops only the tutor's own processes and frees all GPU memory, so do this before gaming or other GPU work. |
| 🌐 **Window closed?** | The tutor keeps running. Reopen it from the shortcut, or go to <http://127.0.0.1:8765> in your browser. |

### Your first 5 minutes

1. Click your name at the bottom left (or press <kbd>Ctrl</kbd>+<kbd>,</kbd>) and set **your name, English level and goals**.
2. Click the course button at the top of the chat (or press <kbd>Ctrl</kbd>+<kbd>K</kbd>) and pick **PSYC 110 → Lecture 1**, or **English Stage 1 → Lesson 01**.
3. Click the **wave icon** next to the microphone (or press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd>) and **just talk**. Headphones give the best experience.
4. Watch your English corrections appear under your messages, then open the **Pronunciation** page and try the **/p/ and /b/** drill.

The full guide to every feature is in **[USER_GUIDE.ar.md](USER_GUIDE.ar.md)** (Arabic).

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| **"No NVIDIA graphics card was found"** | The app needs an NVIDIA GPU. On a laptop with two GPUs, make sure the NVIDIA driver is installed. |
| **"The tutor did not start"** message | Open `logs\start.err.log`, then `logs\server.err.log` and `logs\ollama-server.err.log` for details. Run `Stop Personal Tutor.cmd`, then start again. |
| **"Not enough free GPU memory"** or the model is slow | Close games, video editors and other AI apps, then run Stop and Start. Check *Task Manager → GPU*. With 12 GB, uninstall the Arabic voice by deleting `workers\arabic_tts\.venv`. |
| **A download failed** | Check your internet connection and run the installer again. It resumes. Some company networks block GitHub or Hugging Face downloads. |
| **Port 8765 or 11434 is already in use** | Another copy of the tutor or a separately installed Ollama is running. Close it (or run `Stop Personal Tutor.cmd`), then start again. |
| **The microphone does nothing** | Allow microphone access for the app window (the lock icon or site settings), and check *Windows Settings → Privacy → Microphone*. |
| **Arabic words are not spoken** | The Arabic voice is optional: run `.\scripts\setup.ps1 -WithArabicVoice`. Details are in `logs\arabic-tts.err.log`. |
| **PowerShell says scripts are disabled** | Use the `.cmd` files, which bypass the policy for this project only. Or run: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`. |

The full installer log is saved to `logs\setup.log`.

---

## 6. Updating, backing up and uninstalling

- **Update:** download the new version, then copy into it your `data\` folder (all your progress) and the `materials\` folders of any courses you created yourself. Run the installer, which skips everything already in place.
- **Back up:** *Settings → Data → Backup*, or copy the `data\` folder.
- **Uninstall:** run `Stop Personal Tutor.cmd`, then delete the project folder and the desktop shortcut. Nothing was installed anywhere else.

## Where things are stored

| Folder | Contents | In Git? |
|---|---|---|
| `data\` | Your database: chats, memories, mistakes, vocabulary, progress, backups | ❌ private |
| `materials\` | Course materials, plus copies of files you upload | ✅ bundled ones |
| `models\` | Language, speech and pronunciation models | ❌ downloaded |
| `tools\` | Portable Ollama, uv, Python, eSpeak NG | ❌ downloaded |
| `logs\` | Server logs and the installer log | ❌ |
