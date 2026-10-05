# Setup guide (Windows, no command line needed)

Step-by-step instructions for running the Invoice Extractor on a Windows PC. For the project overview and results, see [README.md](README.md).

---

## Part 1: Start the app on your Windows PC (about 10 minutes the first time)

### Step 1: Check you have the right Python

Python is the programming language this app is written in. It just has to be installed; you never
need to open it yourself.

- **Already installed the newest Python (3.14)?** That should work, so go to Step 2.
- **Nothing installed, or something seems broken?** Install **Python 3.13**, the safest choice:
  1. Go to **https://www.python.org/downloads/windows/**
  2. Under **Python 3.13** (the newest 3.13.x), click **"Windows installer (64-bit)"**.
  3. Run the downloaded file. On the **first screen**, tick the box
     **"Add python.exe to PATH"** at the bottom. This is important.
  4. Click **Install Now** and wait for "Setup was successful."

> **Common mix-up:** If you open "Python" from the Start menu, you get a black window with `>>>`.
> That's Python itself, not where setup commands go. You won't need that window for this project,
> so just close it.

### Step 2: Download and unzip the project

1. On the project's GitHub page, click the green **Code** button → **Download ZIP**.
   Then find the downloaded zip in your **Downloads** folder.
2. **Right-click it → "Extract All..." → Extract.**
3. Move the extracted folder somewhere easy, like your **Desktop** or **Documents**.

> Don't skip "Extract All". Running files while they're still inside the zip won't work.

### Step 3: Double-click `start_app.bat`

Open the extracted folder and **double-click `start_app.bat`**.

- A black window opens and shows progress. **The first time, it takes 2–5 minutes** to download what it needs.
  After that, it starts in a few seconds.
- If Windows shows **"Windows protected your PC"**, click **More info → Run anyway**.
  (Windows warns about any downloaded file it doesn't recognize.)
- When it's ready, **your web browser opens the app automatically**.
  If it doesn't, open your browser and go to **http://localhost:8501**

**Keep the black window open while you use the app. Closing it turns the app off.**

### Step 4: Add your API key

In the app's left sidebar there's a box labeled **"Anthropic API key"**. Paste your key there
(it starts with `sk-ant-`). The app only keeps it until you close it, so you'll paste it again next time.

> **Optional, so you don't have to paste it every time:** in the project folder, open the
> `.streamlit` folder, make a copy of `secrets.toml.example`, rename the copy to `secrets.toml`, open it
> with Notepad, replace `sk-ant-...` with your real key (keep the quote marks), and save.
> Never share or upload that file.

### Step 5: Try it

1. **"Try an invoice" tab:** pick a sample invoice and click **Extract data**. You'll see the invoice on
   the left, and on the right what the AI found, what the right answer was, ✅/❌ for each field, and what
   it cost.
2. **"Accuracy & cost report" tab:** click **Run evaluation** to process all 30 samples (about 2 minutes,
   well under $1). Then switch the model in the sidebar to **claude-haiku** and run it again to compare a
   cheaper model.
3. Scroll down to **Business case** and change the numbers (invoices per month, staff cost) to see the
   monthly savings.
4. When you're ready to improve the score, follow **"Tuning, the fair way"** below.

**To stop:** close the black window. **To start again later:** double-click `start_app.bat`.

---

## Tuning, the fair way

The sidebar has an **Invoice set** switch with two sets of 30 invoices:

- **Practice set:** study it and tune on it as much as you like.
- **Test set (held out):** different invoices, including **2 layouts that never appear in the practice set**.
  Don't study it. Score it **once**, at the end. That's the honest number to report.

The loop, all inside the app:

1. Sidebar on **Practice set** → **Accuracy & cost report** → **Run evaluation**.
2. **Tune** tab: read the list of what the AI got wrong.
3. In the same tab, edit the instructions to fix the *kind* of mistake (e.g. "European dates are day-first"),
   then **Save as new version** (v1, v2, ...).
4. Run the practice set again. The report compares versions side by side. Keep changes that help.
5. Done tuning? Switch the sidebar to **Test set** and run it once.

Your instructions are saved in `instructions.txt` in the main folder. **Reset to default** in the Tune tab undoes everything.

---

## Reviewing invoices like an analyst (Review tab)

This is how a person works alongside the AI in production:

- **Flagged queue:** every invoice the AI marked as uncertain (or that failed). The flag reason is shown at the top.
- **Spot-check queue:** a random sample of invoices the AI did *not* flag (10% by default). If you have to
  correct some of these, the AI is making mistakes it doesn't know about. The **spot-check error rate** measures that.

For each invoice: compare the image on the left to the fields on the right, fix anything wrong, and click
**Save review**. Saving with no edits marks it ✅ approved; any edit marks it ✏️ corrected. The next invoice
opens automatically. Reviews are kept in the `reviews` folder, so you can stop and come back.

**Download results with corrections (CSV)** gives one row per invoice, with your corrections where you
reviewed and the AI's values elsewhere. That's what would go into the client's accounting system.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| Black window says **"I couldn't find Python"** | Do Step 1 (install Python 3.13, tick "Add python.exe to PATH"), then double-click `start_app.bat` again. |
| Black window flashes and disappears instantly | Make sure you extracted the zip (Step 2) and are double-clicking the file inside the extracted folder. |
| **"Windows protected your PC"** | Click **More info → Run anyway**. |
| Lots of red text while "Checking required packages" | Install Python 3.13 (Step 1), then **delete the `.venv` folder** inside the project folder and double-click `start_app.bat` again. |
| Browser didn't open | Go to **http://localhost:8501** yourself. |
| Browser says "can't reach this page" | The black window was closed. Double-click `start_app.bat` again. |
| App says **"Add an API key"** | Paste your key in the sidebar (Step 4). |
| Extraction error mentioning **credit** or **billing** | Add credit to your account at console.anthropic.com. |
| Anything else | Click in the black window, press **Ctrl+A** (select all) then **Ctrl+C** (copy) to copy the error message for troubleshooting. |

> **Can't see the `.venv` or `.streamlit` folders?** In File Explorer click **View → Show → Hidden items**.

---

## Part 2 (optional): Put it online with a shareable link

This runs the app on a free website instead of your PC, so nothing needs to be installed.

1. Make a free account at **github.com**, click **New repository**, give it a name, and create it.
2. On the new repository page, click **"uploading an existing file"** and drag in everything from
   the project folder **except** `.venv`, `__pycache__` folders and any `secrets.toml` file.
   (`.gitignore` does not apply to drag-and-drop uploads, so leave these out by hand.)
3. Go to **share.streamlit.io**, sign in with GitHub, click **Create app**, choose your repository,
   and set the main file to `app.py`. Under **Advanced settings**, choose Python 3.13 or 3.14.
4. In the app's **Settings → Secrets**, paste this line with your real key:
   `ANTHROPIC_API_KEY = "sk-ant-..."`

Anyone with the link can run extractions on your key, so set a low monthly spending limit in the
Anthropic console, or skip the secret and let visitors paste their own key.

**Results saved on the website aren't permanent.** Run evaluations on your PC, then upload the new files
from `results/` to GitHub; the website picks them up automatically.
