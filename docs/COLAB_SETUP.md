# Google Colab integration - setup (official `colab` CLI)

The platform launches the generated Gradio app on a Google Colab runtime through Google's official command-line tool
(PyPI `google-colab-cli`, command `colab`). It only ever runs the documented sub-commands `new`, `sessions`, `status`,
`exec`, `stop`, `version` (global option `--auth` first). Nothing is simulated: every state shown in the UI comes from a
real CLI call, and the public `https://....gradio.live` link is shown only after the app reports its model loaded **and**
the link answers an HTTP request from your PC.

## Windows: read this first

Google's README for the CLI says: **"Linux and macOS only. Windows is not supported at this time."** This project runs
on native Windows 11 and does not use WSL, Docker or a VM, so the Windows path is **experimental**:

- The adapter launches `colab` natively (no shell, UTF-8 forced, process tree killed on cancel/timeout).
- Whether Google's CLI itself starts on Windows depends on its Python dependencies. The app never assumes it works:
  **Check status** makes a real authenticated call and shows the error (in full under **View logs**) if the CLI crashes.
- If the CLI cannot run on Windows, the supported fallback that needs no Linux is the generated notebook:
  upload `colab_notebook.ipynb` at https://colab.research.google.com (File > Upload notebook), pick a GPU runtime,
  then Run all. The Colab runtime itself is Linux; that is fine.

## 1. Install the CLI (PowerShell, inside the project folder)

```powershell
.\.venv\Scripts\python.exe -m pip install google-colab-cli
```

(or `uv tool install google-colab-cli`). If `colab` is not on PATH, put its full path in `.env`:

```
COLAB_COMMAND=D:\Model To Gradio\.venv\Scripts\colab.exe
```

`.bat`/`.cmd`/`.ps1` launchers are refused on purpose; point at `colab.exe`.

## 2. Check, then sign in (only when you choose to)

```powershell
.\.venv\Scripts\python.exe scripts\real_colab_smoke.py
```

This is read-only: it finds the CLI, checks it is Google's, and runs `colab sessions`. It never signs in and never
creates a runtime.

In the app (`run_platform.bat`, then http://127.0.0.1:7860 > **3. Run on Google Colab**):

1. The page checks the connection once when it opens; **Check status** repeats that real `colab sessions` check.
2. **Sign in with Google** > open the Google page > approve > paste the code Google shows > **Submit code**.
   The app relays the CLI's own copy-paste OAuth flow. No password is entered here; the pasted code is never logged.
   The CLI stores its own token; this app never reads it. To sign out, delete the CLI's saved token
   (`~\.config\colab-cli\token.json`) and revoke access at https://myaccount.google.com/permissions.
3. "Connected" appears only after `colab sessions` succeeds against Colab.

`COLAB_AUTH=adc` switches to gcloud credentials (the Sign-in button then shows the `gcloud auth application-default login` command instead of starting a flow).

## 3. Launch

Generate an app from a model URL, choose a runtime (a *request*: your account/plan and Colab availability decide),
optionally set a link username/password, click **Launch on Colab**. Steps, each verified:
validate the generated code > create runtime > verify the GPU with `nvidia-smi` > `pip install` the validated
requirements > transfer exactly `app.py`, `requirements.txt`, a fixed launcher (and the optional password file) with
SHA-256 verification > start detached > wait for model-loaded + share URL > check the URL from this PC.
**Stop Colab runtime** releases the runtime this app started (during a launch it cancels the launch); any failure releases it automatically.
Always stop the runtime when done: runtimes use compute units and the public link works for anyone who has it.

## Limits (honest list)

- Only tasks the generator supports are launched; unsupported models get an analysis and a reason, not an app.
- `trust_remote_code` apps and gated/private models are refused (your Hugging Face token is never sent to Colab).
- Image/audio outputs are produced by the Gradio app on Colab and viewed at its public link; nothing is relayed locally.
- The self-test runs the app's own first example when it has one; "running" does not prove prediction quality.
- Free-tier GPUs are not guaranteed; Google may refuse or reclaim a runtime (the app reports it and removes stale links).
- `m2g/colab_runtime.py` is a legacy client for the beta, allowlist-only Colab API. The panel does not use it.
