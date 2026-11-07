# RUN_GUIDE - Model to Gradio on Windows 11 (PowerShell)

Every command below was checked against the real project files (`requirements*.txt`, the `.bat` scripts, `m2g/web.py`,
`m2g/cli.py`, `.env.example`). Commands work in **Windows PowerShell 5.1** (the Windows 11 default) and PowerShell 7.

## What has and has not been verified

| Status | What |
|---|---|
| **Verified** (Linux sandbox, Python 3.12, simulated Hugging Face/GitHub network) | The 110 automated tests pass; generated apps and notebooks pass the static safety checks; the notebook's environment-check cell runs. |
| **Not verified** | Anything on your Windows machine; the `.bat` scripts; `pip install`; Gradio actually starting and the page rendering; live Hugging Face/GitHub access; real model inference (local or Colab); opening the notebook in Colab. |

If a step behaves differently from this guide, the guide is wrong, not you. Copy the exact message and report it.

Steps marked **[sandbox-verified]** or **[untested]** below tell you which kind of step it is.

---

## 1. Open the project and check Python

```powershell
cd "D:\Model To Gradio"
python --version
```
You need **Python 3.10 or newer** (Gradio 5 requires it). Tested only on 3.12.
If `python` is not recognised or opens the Microsoft Store, install Python from https://www.python.org/downloads/
(tick **Add python.exe to PATH**), close PowerShell, reopen it, and repeat. `py --version` is an alternative; if you use
`py`, write `py -3 -m venv .venv` in step 2.

## 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
Your prompt should now start with `(.venv)`. Confirm Python comes from the environment:
```powershell
python -c "import sys; print(sys.executable)"
```
The path must end in `Model To Gradio\.venv\Scripts\python.exe`.

**"running scripts is disabled on this system"?** Allow scripts for this PowerShell window only, then activate again:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```
(Or skip activation entirely and use `.\.venv\Scripts\python.exe` wherever this guide says `python`.)

> Shortcut: `.\setup_windows.bat` does steps 2-3 and also copies `.env.example` to `.env`. **[untested]** Use it or the
> manual steps, not both.

## 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -c "import gradio; print('gradio', gradio.__version__)"
```
`requirements-dev.txt` includes `requirements.txt` (`gradio>=5.0`) plus `pytest>=8.0` and `nbformat>=5.9`. The platform itself
needs only Gradio; it does **not** install torch or transformers. **[untested]**

`.env` is optional and holds tokens; see section 9.

## 4. Run the automated tests

```powershell
$env:PYTHONPATH = "$PWD;$PWD\tests"
python -m unittest discover -s tests -p "test_*.py"
```
(Same as `.\run_tests.bat`.) Expected ending: `Ran 110 tests in ...s` then `OK`. A few lines of ordinary output in the middle
(for example a line starting `### Failed at stage parse`) are normal: they come from tests that deliberately feed bad URLs.
**[sandbox-verified; Windows run untested]** The tests use a fake network and download nothing. Use `unittest`; `pytest`
is installed and `pytest.ini` exists, but it has not been run.

## 5. Start the platform and open the frontend

```powershell
$env:PYTHONPATH = "$PWD"
python -m m2g.web
```
(Same as `.\run_platform.bat`.) The window stays busy while the server runs: leave it open.
The platform listens only on this computer at:

**http://127.0.0.1:7860**

It does **not** open the browser by itself. Either paste the URL into your browser, or, from a **second** PowerShell window:
```powershell
Start-Process "http://127.0.0.1:7860"
```
Gradio prints a `Running on local URL` line when it is ready. **[untested]**

Different port (see port conflicts in section 9):
```powershell
$env:M2G_PORT = "7861"
python -m m2g.web
```
then open `http://127.0.0.1:7861`.

## 6. Generate a Gradio app

On the page:
1. Paste the URL into the **Repository URL** box. Accepted forms: `https://huggingface.co/<owner>/<model>` or
   `https://github.com/<owner>/<repo>`. Three clickable examples sit below the button.
2. Click **Analyze and generate** (or press Enter in the box).
3. Read the summary under the button: status, detected task with the evidence used, inputs/outputs, hardware estimate, warnings.
4. Tabs: **app.py** (the generated code), **requirements.txt**, **Downloads** (all generated files).

Outcomes:
- `supported` / `supported_with_warnings`: app, requirements, notebook and analysis are written.
- `unsupported` or `partial` (every GitHub repo is `partial`): only `analysis.json` is written, and the summary says
  **Why it cannot be generated**. See section 9.

Try first: `https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english` (small, text classification).
The platform reads only metadata; it downloads no weights and runs no repository code.

Prefer no browser? `python -m m2g.cli https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english`
prints the same summary (exit code 0 = generated, 2 = unsupported, 1 = error). **[untested live]**

## 7. Where files are saved, and how to run the generated app

Files go to `D:\Model To Gradio\output\<owner>__<model>\`, for example
`output\distilbert__distilbert-base-uncased-finetuned-sst-2-english\`, containing:

| File | Purpose |
|---|---|
| `app.py` | the Gradio app |
| `requirements.txt` | what `app.py` needs (torch, transformers, gradio, ... per model) |
| `colab_notebook.ipynb` | notebook for Google Colab |
| `analysis.json` | the evidence behind every decision |

Names longer than 80 characters are shortened. Generating the same model again overwrites its folder.
```powershell
explorer "D:\Model To Gradio\output"
```

**Run the generated app locally** (sensible only for small, CPU-friendly models; the summary says when that applies, otherwise
use Colab). Use a separate environment, because torch is large:
```powershell
deactivate
cd "D:\Model To Gradio\output\distilbert__distilbert-base-uncased-finetuned-sst-2-english"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Open the URL Gradio prints. If the platform is still running it already uses port 7860; Gradio normally picks the next free
port and prints it, or stop the platform first. **[untested]** The model downloads on your **first click**, not at startup, so
the first prediction is slow. Test: type a sentence and submit; you should see class scores. For a gated model set the token
in this window first (the generated app reads only the environment, not `.env`):
```powershell
$env:HF_TOKEN = "hf_your_token_here"
```

## 8. Run the notebook in Google Colab

1. Open https://colab.research.google.com and sign in.
2. **File > Upload notebook**, **Upload** tab, choose `D:\Model To Gradio\output\<owner>__<model>\colab_notebook.ipynb`.
3. If the notebook's first text cell recommends a GPU: **Runtime > Change runtime type > T4 GPU > Save**.
   Free GPUs are not guaranteed; a Google login alone does not give you one.
4. Gated model only: click the **key icon** (Secrets) in the left sidebar, add `HF_TOKEN`, and switch on notebook access.
5. **Runtime > Run all**. In order, the cells: check GPU/RAM (stops with a clear message if the runtime is too small),
   install packages, read the token, write `app.py`, load the model (first run downloads the weights) and run a self-test,
   launch the UI.
6. Open the `gradio.live` link printed at the bottom. The last cell has `SHARE_PUBLIC_LINK = True`: anyone with that link can
   use your Colab GPU until you stop the cell. Change it to `False` to keep it private.

After the install cell, if pip reports conflicts or imports fail: **Runtime > Restart session**, then **Run all**.
**[untested in Colab; only the notebook's structure and its first cell were checked]**

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `python` not recognised / Microsoft Store opens | Install Python from python.org with "Add to PATH", reopen PowerShell. |
| `Activate.ps1 cannot be loaded ... scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, activate again. |
| `No module named 'gradio'` (or `pytest`) | Environment not active or install incomplete. Check the `(.venv)` prompt and `python -c "import sys; print(sys.executable)"`, then repeat step 3. |
| `No module named 'm2g'` | You are not in `D:\Model To Gradio`. `cd` there; set `$env:PYTHONPATH = "$PWD"`. |
| `No module named 'fakehub'` (tests) | Use the step 4 commands, including the `tests` entry in PYTHONPATH. |
| `pip` fails: timeout, SSL, "could not find a version" | Network, VPN or firewall problem. Retry on another network, or `python -m pip install -r requirements-dev.txt --retries 2`. |
| Server fails to start, mentions no empty port / address in use | Port 7860 is taken (maybe an earlier run). The platform prefers 7860 but automatically moves to the next free port, and prints the URL it actually uses. To reuse 7860, find the old process: `Get-NetTCPConnection -LocalPort 7860 -State Listen \| Select-Object OwningProcess` then `Get-Process -Id <number>`; stop it with `Stop-Process -Id <number>`, or pick another starting point with `$env:M2G_PORT = "7861"`. |
| Colab run: "Gradio did not create a public share link" | The app started but the gradio.live tunnel could not log in (tunnel service down or egress blocked). The tool now retries the tunnel 3 times (5 s apart) and reports the real reason, e.g. `login to server failed: ... connection refused`. Nothing is left running; click Launch again. |
| `Failed at stage inspect` with a network message | No route to huggingface.co / github.com (DNS, firewall, VPN). The platform connects **directly** and has no proxy support, so a proxy-only network will fail. Fix connectivity and click again. |
| GitHub `403` | Unauthenticated rate limit. Put `GITHUB_TOKEN=...` in `.env`, restart the platform. |
| Hugging Face 401/403, "gated" | Accept the licence on the model page, put `HF_TOKEN=...` in `.env`, restart the platform. Tokens go only to their own host and are never written to output. |
| `Failed at stage parse` | URL rejected for safety (not https, wrong host, extra path pieces, credentials in URL, IP address). Use a plain `https://huggingface.co/<owner>/<model>` link. |
| Status `unsupported` / "Why it cannot be generated" | Intended, not a crash. Typical reasons: task not supported (depth estimation, image-to-image, text-to-video, ...); non-transformers formats (GGUF, ONNX, Keras, spaCy, scikit-learn, ...); GPTQ/AWQ/FP8 weights; pickle-only weights; model needs `trust_remote_code`; too large for free Colab; unclear task metadata. Pick another model. The full list is in `README.md`. |
| Opt-ins for the above | `M2G_ALLOW_PICKLE_WEIGHTS=1` / `M2G_ALLOW_REMOTE_CODE=1` in `.env` (restart). Both can run arbitrary code from a repo; use only on code you reviewed. |
| `Cannot write to the output folder` | Folder not writable. Set `M2G_OUTPUT_DIR` in `.env` to a folder you own. |
| Generated app: out of memory / very slow | Model too big for your PC. Use the Colab notebook. |
| Notebook raises "This model needs a GPU" | Runtime > Change runtime type > T4 GPU, then Run all. |
| Tests fail | Copy the whole output; do not edit the tests. |

## 10. Stop the server, come back later

Stop: click the PowerShell window running the server and press **Ctrl+C**. If you closed that window but port 7860 is still
busy, use the `Get-NetTCPConnection` and `Stop-Process` commands from section 9.

Leave the environment: `deactivate`

Later (new PowerShell window):
```powershell
cd "D:\Model To Gradio"
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$PWD"
python -m m2g.web
```
or simply `.\run_platform.bat`, which needs no activation. `$env:` settings last only for that window; put permanent
settings in `.env`. Edits to `.env` apply when the platform is restarted.

---

## Gaps noticed while writing this guide (code not changed)

1. The "Locally" hint the UI prints after generation uses `&&` and `.venv\Scripts\activate`, which does not work in Windows
   PowerShell 5.1. Use the commands in section 7.
2. The platform never opens the browser for you; open the URL yourself.
3. The generated app ignores `.env`; set `$env:HF_TOKEN` in the window that runs it.
4. The platform has no proxy support.
5. `D:\Model To Gradio` is not a Git repository, so there is no history or rollback.
6. `model_to_gradio\` is an unused leftover and can be deleted.
