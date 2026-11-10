# First-run checklist

The automated tests use a fake network, so these steps confirm the parts they cannot: your Python install, live
Hugging Face access, the real Gradio UI, and a real model.

1. **Tests:** `run_tests.bat`. Expect `OK` (110 tests when this file was written). If anything fails, copy the output.
2. **Live inspection, no UI:**
   `.venv\Scripts\python -m m2g.cli https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english`
   Expect status `supported`, task `text-classification`, and files in `output\distilbert__distilbert-base-uncased-finetuned-sst-2-english\`.
   - `Failed at stage inspect` with a network message: DNS/proxy/firewall problem; the platform does not retry dead hosts.
   - GitHub `403`: unauthenticated rate limit; set `GITHUB_TOKEN` in `.env`.
3. **UI:** `run_platform.bat`, open http://127.0.0.1:7860, paste the same URL. Check the app.py / requirements / downloads tabs.
4. **Small local inference (optional, ~270 MB download):**
   ```
   cd output\distilbert__distilbert-base-uncased-finetuned-sst-2-english
   python -m venv .venv && .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
   Type a sentence; you should get POSITIVE/NEGATIVE scores.
5. **Colab:** upload `colab_notebook.ipynb`, select a GPU runtime, *Run all*. Cell 1 prints the real GPU/RAM; the last cell
   prints a Gradio link.

Report any failure with the exact message; unsupported or failing models should always produce a plain-language error, never a
silent blank result.
